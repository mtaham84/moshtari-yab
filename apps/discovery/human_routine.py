"""
Human Behavioral Routine & Shift Planning Module.

Simulates authentic human consultant behavior:
1. Plans dynamic daily shifts (6 to 7 variable hours per day) using LLM or stochastic fallback.
2. Divides work into realistic morning and afternoon/evening shifts with an offline lunch/rest window.
3. Generates non-linear delays and periodic micro-breaks (coffee/call breaks every 3-4 actions).
"""
from __future__ import annotations

import datetime
import json
import logging
import random
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple
from zoneinfo import ZoneInfo

logger = logging.getLogger("discovery.human_routine")
TEHRAN_TZ = ZoneInfo("Asia/Tehran")


@dataclass
class DailyShiftSchedule:
    date: datetime.date
    morning_start: datetime.time
    morning_end: datetime.time
    afternoon_start: datetime.time
    afternoon_end: datetime.time
    total_work_minutes: int
    generated_by: str = "fallback"  # 'llm' or 'fallback'

    def to_dict(self) -> Dict[str, Any]:
        return {
            "date": self.date.isoformat(),
            "morning_start": self.morning_start.strftime("%H:%M"),
            "morning_end": self.morning_end.strftime("%H:%M"),
            "afternoon_start": self.afternoon_start.strftime("%H:%M"),
            "afternoon_end": self.afternoon_end.strftime("%H:%M"),
            "total_work_minutes": self.total_work_minutes,
            "total_work_hours": round(self.total_work_minutes / 60.0, 2),
            "generated_by": self.generated_by,
        }


class HumanShiftPlanner:
    """Manages dynamic working hours and realistic human pacing."""

    _cached_schedules: Dict[datetime.date, DailyShiftSchedule] = {}

    @classmethod
    def get_or_create_daily_schedule(
        cls,
        target_date: Optional[datetime.date] = None,
        use_llm: bool = True,
    ) -> DailyShiftSchedule:
        if target_date is None:
            target_date = datetime.datetime.now(TEHRAN_TZ).date()

        if target_date in cls._cached_schedules:
            return cls._cached_schedules[target_date]

        schedule = cls.generate_daily_schedule(target_date, use_llm=use_llm)
        cls._cached_schedules[target_date] = schedule
        return schedule

    @classmethod
    def generate_daily_schedule(
        cls,
        target_date: datetime.date,
        use_llm: bool = True,
    ) -> DailyShiftSchedule:
        """
        Generates a 6 to 7 hour (360 to 420 minutes) working schedule for target_date.
        Uses LLM if available, otherwise employs stochastic Gaussian fallback.
        """
        if use_llm:
            try:
                llm_sched = cls._generate_with_llm(target_date)
                if llm_sched:
                    logger.info("Generated daily human shift schedule via LLM for %s: %s", target_date, llm_sched.to_dict())
                    return llm_sched
            except Exception as exc:
                logger.warning("LLM daily schedule generation failed (%s), falling back to stochastic planner.", exc)

        return cls._generate_stochastic_fallback(target_date)

    @classmethod
    def _generate_with_llm(cls, target_date: datetime.date) -> Optional[DailyShiftSchedule]:
        """Prompts LLM to plan realistic Iranian business consulting shifts."""
        from instagram_engine.analyzer import _call_llm_completion

        prompt = (
            f"You are a human behavioral scheduling engine for an Iranian medical/dental clinic consultant on {target_date}.\n"
            f"Create a realistic daily working schedule with EXACTLY TWO shifts (Morning and Afternoon/Evening).\n"
            f"Rules:\n"
            f"1. Total work duration MUST be strictly between 360 and 420 minutes (6.0 to 7.0 hours).\n"
            f"2. Morning shift starts between 09:30 and 10:45 AM, ends between 13:00 and 14:00.\n"
            f"3. Afternoon shift starts between 16:00 and 17:00 (allowing lunch/prayer break), ends between 19:30 and 20:45.\n"
            f"Output ONLY a valid JSON object with keys: morning_start, morning_end, afternoon_start, afternoon_end.\n"
            f'Example: {{"morning_start": "10:15", "morning_end": "13:35", "afternoon_start": "16:25", "afternoon_end": "19:40"}}'
        )

        payload = {
            "messages": [
                {"role": "system", "content": "You output strictly valid JSON without explanation or markdown."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 120,
        }

        raw = _call_llm_completion(payload, timeout=12.0)
        if not raw:
            return None

        # Extract JSON
        match = re.search(r"\{[^{}]+\}", raw)
        if not match:
            return None

        data = json.loads(match.group(0))
        m_start = datetime.datetime.strptime(data["morning_start"], "%H:%M").time()
        m_end = datetime.datetime.strptime(data["morning_end"], "%H:%M").time()
        a_start = datetime.datetime.strptime(data["afternoon_start"], "%H:%M").time()
        a_end = datetime.datetime.strptime(data["afternoon_end"], "%H:%M").time()

        # Compute durations in minutes
        d_morning = (m_end.hour * 60 + m_end.minute) - (m_start.hour * 60 + m_start.minute)
        d_afternoon = (a_end.hour * 60 + a_end.minute) - (a_start.hour * 60 + a_start.minute)
        total_mins = d_morning + d_afternoon

        if 340 <= total_mins <= 440 and m_start < m_end < a_start < a_end:
            return DailyShiftSchedule(
                date=target_date,
                morning_start=m_start,
                morning_end=m_end,
                afternoon_start=a_start,
                afternoon_end=a_end,
                total_work_minutes=total_mins,
                generated_by="llm",
            )
        return None

    @classmethod
    def _generate_stochastic_fallback(cls, target_date: datetime.date) -> DailyShiftSchedule:
        """Deterministic, highly varied stochastic schedule between 360 and 420 minutes."""
        target_work_minutes = random.randint(360, 420)  # 6h 0m to 7h 0m

        # Morning shift: ~3 hours to 3.5 hours
        morning_duration = random.randint(180, 210)
        afternoon_duration = target_work_minutes - morning_duration

        # Morning start: between 09:45 and 10:45
        m_start_hour = random.choice([9, 10])
        m_start_min = random.randint(15, 55) if m_start_hour == 9 else random.randint(0, 35)

        m_start_total = m_start_hour * 60 + m_start_min
        m_end_total = m_start_total + morning_duration

        # Lunch & rest break: 2.5 to 3.2 hours break
        break_duration = random.randint(150, 190)
        a_start_total = m_end_total + break_duration
        a_end_total = a_start_total + afternoon_duration

        def _to_time(total_min: int) -> datetime.time:
            total_min = min(total_min, 23 * 60 + 59)
            h = total_min // 60
            m = total_min % 60
            return datetime.time(h, m)

        m_start = _to_time(m_start_total)
        m_end = _to_time(m_end_total)
        a_start = _to_time(a_start_total)
        a_end = _to_time(a_end_total)

        sched = DailyShiftSchedule(
            date=target_date,
            morning_start=m_start,
            morning_end=m_end,
            afternoon_start=a_start,
            afternoon_end=a_end,
            total_work_minutes=target_work_minutes,
            generated_by="stochastic",
        )
        logger.info("Generated stochastic daily shift schedule for %s: %s", target_date, sched.to_dict())
        return sched

    @classmethod
    def is_shift_active(
        cls,
        now_dt: Optional[datetime.datetime] = None,
        schedule: Optional[DailyShiftSchedule] = None,
    ) -> Tuple[bool, str, int]:
        """
        Evaluates whether the current moment falls within active working shift hours.
        Returns:
            (is_active: bool, status_reason: str, wait_seconds: int)
        """
        if now_dt is None:
            now_dt = datetime.datetime.now(TEHRAN_TZ)
        elif now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=TEHRAN_TZ)

        current_date = now_dt.date()
        if schedule is None or schedule.date != current_date:
            schedule = cls.get_or_create_daily_schedule(current_date)

        curr_time = now_dt.time()
        c_min = curr_time.hour * 60 + curr_time.minute
        m_start = schedule.morning_start.hour * 60 + schedule.morning_start.minute
        m_end = schedule.morning_end.hour * 60 + schedule.morning_end.minute
        a_start = schedule.afternoon_start.hour * 60 + schedule.afternoon_start.minute
        a_end = schedule.afternoon_end.hour * 60 + schedule.afternoon_end.minute

        # 1. Inside Morning Shift
        if m_start <= c_min < m_end:
            left_min = m_end - c_min
            return True, f"شیفت صبح فعال است (تا {schedule.morning_end.strftime('%H:%M')} - {left_min} دقیقه مانده)", 0

        # 2. Inside Afternoon Shift
        if a_start <= c_min < a_end:
            left_min = a_end - c_min
            return True, f"شیفت عصر فعال است (تا {schedule.afternoon_end.strftime('%H:%M')} - {left_min} دقیقه مانده)", 0

        # 3. Before Morning Shift (Early morning / night)
        if c_min < m_start:
            wait_sec = (m_start - c_min) * 60 - now_dt.second
            return False, f"ساعات استراحت شبانه / قبل از شیفت صبح (شروع شیفت در {schedule.morning_start.strftime('%H:%M')})", max(wait_sec, 60)

        # 4. Lunch & Rest Break between morning and afternoon
        if m_end <= c_min < a_start:
            wait_sec = (a_start - c_min) * 60 - now_dt.second
            return False, f"استراحت نیمروزی و ناهار (شروع شیفت عصر در {schedule.afternoon_start.strftime('%H:%M')})", max(wait_sec, 60)

        # 5. After Afternoon Shift (Evening / Night shutdown)
        # Wait until tomorrow morning shift
        tomorrow = current_date + datetime.timedelta(days=1)
        tomorrow_sched = cls.get_or_create_daily_schedule(tomorrow)
        t_m_start = tomorrow_sched.morning_start.hour * 60 + tomorrow_sched.morning_start.minute
        mins_until_midnight = (24 * 60) - c_min
        total_wait_sec = (mins_until_midnight + t_m_start) * 60 - now_dt.second
        return False, f"پایان شیفت‌های کاری امروز (شروع شیفت فردا در {tomorrow_sched.morning_start.strftime('%H:%M')})", max(total_wait_sec, 60)

    @staticmethod
    def calculate_next_action_delay(
        account_tier: str = "AGED",
        consecutive_actions: int = 0,
        is_priority_reply: bool = False,
    ) -> Tuple[float, bool]:
        """
        Computes realistic non-linear delay between outbound actions.
        Every 3 to 4 actions, triggers a 'coffee/phone call' micro-break (25 to 40 minutes).
        Priority replies (answering online leads in chat) receive shorter typing/pacing delays (60-120s).
        Returns:
            (delay_seconds: float, is_micro_break: bool)
        """
        # Priority direct replies to ongoing customer negotiations
        if is_priority_reply:
            # 60 to 120 seconds typing and review pause
            return random.uniform(60.0, 130.0), False

        # Check for realistic human micro-break (coffee, phone call, stepping away)
        # Triggered after every 3 or 4 cold actions
        if consecutive_actions > 0 and consecutive_actions % random.choice([3, 4]) == 0:
            micro_break_sec = random.uniform(1500.0, 2400.0)  # 25 to 40 minutes
            logger.info("☕ Triggering natural human micro-break: %.1f minutes pause.", micro_break_sec / 60.0)
            return micro_break_sec, True

        # Standard cold action delays based on account tier
        if account_tier == "NEW":
            # 25 to 45 minutes
            delay = random.uniform(1500.0, 2700.0)
        elif account_tier == "MEDIUM":
            # 12 to 24 minutes
            delay = random.uniform(720.0, 1440.0)
        else:  # AGED
            # 8 to 16 minutes with realistic second-level jitter
            delay = random.uniform(480.0, 960.0)

        # Add small sub-minute stochastic jitter
        delay += random.uniform(-25.0, 35.0)
        return max(delay, 120.0), False
