"""
Injectable LLM Gateway (P05.03 / R14, R41).

Orchestrates multi-model fallback execution with:
1. Strict budget pre-reservation (Insufficient credit -> QUEUED, zero network calls).
2. Cross-worker provider rate-limit & quota checks.
3. Injectable transport layer (MockTransport for hermetic tests, HttpLLMTransport for HTTP).
4. Granular error classification (NETWORK_TIMEOUT, QUOTA_429, PROVIDER_5XX, PARSE_ERROR).
5. Immutable ledger accounting without blind external retries on persistence failures.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple

from apps.core.gateway_contract import (
    ModelRef,
    OperationRef,
    AttemptRef,
    PriceSnapshot,
    UsageResult,
    GatewayResultStatus,
    GatewayResult,
    BudgetPort,
    QuotaPort,
    LedgerPort,
)

logger = logging.getLogger("core.gateway")


class GatewayException(Exception):
    """Base exception for LLM gateway failures."""
    pass


class ProviderQuotaExhausted(GatewayException):
    """Provider returned 429 or quota limit hit."""
    pass


class ProviderNetworkError(GatewayException):
    """Socket dropped or connection timed out."""
    pass


class ProviderResponseError(GatewayException):
    """HTTP 5xx or server response failure."""
    pass


# =====================================================================
# Transport Interfaces & Implementations
# =====================================================================

class TransportResponse:
    def __init__(
        self,
        content: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        raw_response: Optional[Dict[str, Any]] = None,
    ):
        self.content = content
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        self.raw_response = raw_response or {}


class LLMTransport(abc.ABC):
    """
    Abstract network transport for OpenAI-compatible completions.
    """

    @abc.abstractmethod
    def call(
        self,
        model_ref: ModelRef,
        base_url: str,
        api_key: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 3500,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> TransportResponse:
        pass


class HttpLLMTransport(LLMTransport):
    """
    Standard library HTTP transport calling /chat/completions.
    """

    def call(
        self,
        model_ref: ModelRef,
        base_url: str,
        api_key: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 3500,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> TransportResponse:
        url = f"{base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model_ref.deployment_id or model_ref.model_name,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                resp_json = json.loads(resp_bytes.decode("utf-8"))

                choices = resp_json.get("choices", [])
                content = choices[0]["message"]["content"] if choices else ""
                usage = resp_json.get("usage", {})
                prompt_tokens = usage.get("prompt_tokens", 0)
                completion_tokens = usage.get("completion_tokens", 0)

                return TransportResponse(
                    content=content,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    raw_response=resp_json,
                )
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace") if hasattr(exc, "read") else str(exc)
            if exc.code == 429:
                raise ProviderQuotaExhausted(f"429 Quota/Rate limit: {err_body}") from exc
            raise ProviderResponseError(f"HTTP {exc.code}: {err_body}") from exc
        except (socket.timeout, TimeoutError, urllib.error.URLError) as exc:
            raise ProviderNetworkError(f"Network error: {exc}") from exc


class MockLLMTransport(LLMTransport):
    """
    In-memory mock transport for unit and integration testing.
    """

    def __init__(self):
        self.handlers: Dict[str, Callable[..., TransportResponse]] = {}
        self.default_response = TransportResponse("{\"status\": \"ok\"}", prompt_tokens=100, completion_tokens=50)
        self.call_history: List[Dict[str, Any]] = []

    def set_handler(self, model_identity: str, handler: Callable[..., TransportResponse]) -> None:
        self.handlers[model_identity.lower()] = handler

    def set_constant_response(self, model_identity: str, content: str, pt: int = 100, ct: int = 50) -> None:
        self.handlers[model_identity.lower()] = lambda *args, **kw: TransportResponse(content, pt, ct)

    def set_error(self, model_identity: str, exc: Exception) -> None:
        def _err(*args, **kw):
            raise exc
        self.handlers[model_identity.lower()] = _err

    def call(
        self,
        model_ref: ModelRef,
        base_url: str,
        api_key: str,
        messages: List[Dict[str, str]],
        max_tokens: int = 3500,
        temperature: float = 0.2,
        timeout: float = 60.0,
    ) -> TransportResponse:
        self.call_history.append({
            "model_ref": model_ref,
            "base_url": base_url,
            "messages": messages,
            "time": time.time(),
        })

        ident = model_ref.identity
        if ident in self.handlers:
            return self.handlers[ident](model_ref, messages)
        return self.default_response


# =====================================================================
# Gateway Implementation
# =====================================================================

@dataclass
class RetryPolicy:
    """
    Explicit gateway retry bounds (P05.05 / R16).
    Eliminates uncontrolled nested loops across layers.
    """
    max_total_attempts: int = 4
    max_parse_retries_per_model: int = 1
    repair_prompt: str = (
        "Your previous output was invalid JSON. Return ONLY the valid JSON object "
        "matching the schema. No explanations, no markdown ticks."
    )


class LLMGateway:
    """
    Central operational LLM Gateway.
    """

    def __init__(
        self,
        budget_port: BudgetPort,
        quota_port: QuotaPort,
        ledger_port: LedgerPort,
        transport: Optional[LLMTransport] = None,
        registry: Optional[Any] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self.budget_port = budget_port
        self.quota_port = quota_port
        self.ledger_port = ledger_port
        self.transport = transport or HttpLLMTransport()
        self.registry = registry
        self.retry_policy = retry_policy or RetryPolicy()

    @staticmethod
    def _extract_json(text: str) -> Optional[Any]:
        if not text:
            return None
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.S)
        try:
            return json.loads(cleaned)
        except ValueError:
            m = re.search(r"\{.*\}", cleaned, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    return None
        return None

    def execute(
        self,
        operation: OperationRef,
        system_prompt: str,
        user_prompt: str,
        price_snapshot: PriceSnapshot,
        candidates: Optional[List[ModelRef]] = None,
        candidate_endpoints: Optional[Dict[str, Tuple[str, str]]] = None,
        stage: Optional[str] = None,
        max_tokens: int = 3500,
        timeout: float = 60.0,
        is_json: bool = True,
    ) -> GatewayResult:
        """
        Executes an LLM operation with budget reservation, multi-model fallback,
        provider quota enforcement, and append-only ledger tracking.
        """
        candidate_list = list(candidates or [])
        endpoints = dict(candidate_endpoints or {})

        # If candidates not provided, load from registry
        if not candidate_list and self.registry and stage:
            candidate_list = self.registry.candidate_model_refs_for(stage)
            for m in candidate_list:
                endpoints[m.identity] = self.registry.endpoint(m.identity)

        if not candidate_list:
            return GatewayResult(
                status=GatewayResultStatus.UNAVAILABLE,
                error_message="No active candidate models configured.",
            )

        # 1. Estimate Cost & Pre-reserve Budget (Product Decision #2 / R31)
        est_prompt_tokens = int(len(system_prompt + user_prompt) / 2.5) + 50
        est_total_tokens = est_prompt_tokens + max_tokens
        # Estimate using first candidate's price
        est_cost_usd = price_snapshot.compute_cost_usd(est_prompt_tokens, max_tokens, is_cached=False)
        est_cost_toman = price_snapshot.compute_cost_toman(est_cost_usd)

        allowed, reason, reservation_id = self.budget_port.reserve(operation, est_cost_toman)
        if not allowed:
            logger.warning(
                "Gateway operation %s QUEUED due to budget rejection: %s",
                operation.operation_id,
                reason,
            )
            return GatewayResult(
                status=GatewayResultStatus.QUEUED,
                error_message=f"Insufficient funds: {reason}",
            )

        # 2. Candidate Fallback Iteration
        attempts: List[AttemptRef] = []
        attempt_counter = 0

        for model_ref in candidate_list:
            if len(attempts) >= self.retry_policy.max_total_attempts:
                break

            attempt_counter += 1
            attempt_id = f"{operation.operation_id}_att_{attempt_counter}"
            attempt = AttemptRef(
                attempt_id=attempt_id,
                operation_id=operation.operation_id,
                attempt_number=attempt_counter,
                model_ref=model_ref,
            )
            attempts.append(attempt)

            # Check Provider Quota / Rate Limits (R15)
            q_allowed, q_reason = self.quota_port.reserve_provider_quota(model_ref, est_total_tokens)
            if not q_allowed:
                attempt.status = GatewayResultStatus.UNAVAILABLE
                attempt.error_message = f"Quota rejected: {q_reason}"
                self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref=f"quota_{q_reason}")
                continue

            # Fetch endpoint info
            endpoint_info = endpoints.get(model_ref.identity)
            if not endpoint_info:
                if self.registry:
                    endpoint_info = self.registry.endpoint(model_ref.identity)
                else:
                    endpoint_info = ("https://api.openai.com/v1", "test-key")
            base_url, api_key = endpoint_info

            # Dispatch call to Transport
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            try:
                resp = self.transport.call(
                    model_ref=model_ref,
                    base_url=base_url,
                    api_key=api_key,
                    messages=messages,
                    max_tokens=max_tokens,
                    timeout=timeout,
                )
            except ProviderQuotaExhausted as exc:
                self.quota_port.record_provider_429(model_ref, retry_after_sec=30.0, is_daily=True)
                attempt.status = GatewayResultStatus.UNAVAILABLE
                attempt.error_message = str(exc)
                self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="429_quota")
                continue
            except ProviderNetworkError as exc:
                attempt.status = GatewayResultStatus.UNKNOWN
                attempt.error_message = str(exc)
                self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="network_timeout")
                continue
            except ProviderResponseError as exc:
                attempt.status = GatewayResultStatus.FAILED
                attempt.error_message = str(exc)
                self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="provider_5xx")
                continue
            except Exception as exc:
                attempt.status = GatewayResultStatus.FAILED
                attempt.error_message = f"Unexpected: {exc}"
                self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="unknown_err")
                continue

            # Check JSON Parsing if required
            parsed = None
            if is_json:
                parsed = self._extract_json(resp.content)
                if parsed is None:
                    attempt.status = GatewayResultStatus.FAILED
                    attempt.error_message = f"Malformed JSON: {resp.content[:200]}"
                    self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="json_parse_err")

                    # Check if format repair retry is allowed on this model
                    if (
                        self.retry_policy.max_parse_retries_per_model > 0
                        and len(attempts) < self.retry_policy.max_total_attempts
                    ):
                        attempt_counter += 1
                        repair_attempt = AttemptRef(
                            attempt_id=f"{operation.operation_id}_att_{attempt_counter}",
                            operation_id=operation.operation_id,
                            attempt_number=attempt_counter,
                            model_ref=model_ref,
                        )
                        attempts.append(repair_attempt)

                        repair_messages = list(messages) + [
                            {"role": "assistant", "content": resp.content},
                            {"role": "user", "content": self.retry_policy.repair_prompt},
                        ]

                        try:
                            repair_resp = self.transport.call(
                                model_ref=model_ref,
                                base_url=base_url,
                                api_key=api_key,
                                messages=repair_messages,
                                max_tokens=max_tokens,
                                timeout=timeout,
                            )
                            parsed_repair = self._extract_json(repair_resp.content)
                            if parsed_repair is not None:
                                resp = repair_resp
                                parsed = parsed_repair
                                attempt = repair_attempt
                            else:
                                repair_attempt.status = GatewayResultStatus.FAILED
                                repair_attempt.error_message = f"Repair JSON failed: {repair_resp.content[:200]}"
                                self.ledger_port.record_attempt(operation, repair_attempt, price_snapshot, ref="repair_parse_err")
                                continue
                        except Exception as exc:
                            repair_attempt.status = GatewayResultStatus.FAILED
                            repair_attempt.error_message = f"Repair call failed: {exc}"
                            self.ledger_port.record_attempt(operation, repair_attempt, price_snapshot, ref="repair_net_err")
                            continue
                    else:
                        continue

            # Successful Call: Compute usage & costs
            cost_usd = price_snapshot.compute_cost_usd(resp.prompt_tokens, resp.completion_tokens, is_cached=False)
            cost_toman = price_snapshot.compute_cost_toman(cost_usd)
            usage = UsageResult(
                prompt_tokens=resp.prompt_tokens,
                completion_tokens=resp.completion_tokens,
                total_tokens=resp.total_tokens,
                is_cached=False,
                cost_usd=cost_usd,
                cost_toman=cost_toman,
                is_exact_usage=True,
            )
            attempt.status = GatewayResultStatus.SUCCESS
            attempt.usage = usage

            # Settle Budget & Quota
            if reservation_id:
                self.budget_port.settle(reservation_id, cost_toman)
            self.quota_port.record_provider_success(model_ref, resp.total_tokens)

            # Record Ledger
            self.ledger_port.record_attempt(operation, attempt, price_snapshot, ref="success")

            return GatewayResult(
                status=GatewayResultStatus.SUCCESS,
                content=resp.content,
                parsed_json=parsed,
                usage=usage,
                model_ref=model_ref,
                attempts=attempts,
                reservation_id=reservation_id,
            )

        # All candidates exhausted or failed: Release reserved funds
        if reservation_id:
            self.budget_port.release(reservation_id)

        last_status = attempts[-1].status if attempts else GatewayResultStatus.FAILED
        last_error = attempts[-1].error_message if attempts else "All providers failed"

        return GatewayResult(
            status=last_status,
            error_message=f"Gateway failed: {last_error}",
            attempts=attempts,
            reservation_id=reservation_id,
        )
