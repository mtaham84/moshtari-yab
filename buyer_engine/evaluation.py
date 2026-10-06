from __future__ import annotations

from .core import ProductProfile, SourceRecord, local_classify

PERSIAN_CASES = [
    ("برای کافه‌ام دنبال دستگاه اسپرسوساز صنعتی هستم", "buy"),
    ("میخوام دستگاه اسپرسوساز بخرم", "buy"),
    ("می‌خواهم یک دستگاه اسپرسوساز تهیه کنم", "buy"),
    ("نیازمند دستگاه اسپرسوساز برای مغازه هستم", "buy"),
    ("برای رستوران دستگاه اسپرسوساز لازم دارم", "buy"),
    ("قصد خرید دستگاه اسپرسوساز دارم", "buy"),
    ("کسی دستگاه اسپرسوساز دست دوم سراغ داره؟", "buy"),
    ("دنبال خرید اسپرسوساز برای کافه می‌گردم", "buy"),
    ("دستگاه اسپرسوساز می‌خرم، پیشنهاد بدید", "buy"),
    ("خریدار دستگاه اسپرسوساز صنعتی هستم", "buy"),
    ("برای راه‌اندازی کافه اسپرسوساز می‌خواهم", "buy"),
    ("یک دستگاه اسپرسوساز لازم دارم، فروشنده دارید؟", "buy"),
    ("برای فروشگاه خودم اسپرسوساز می‌خوام", "buy"),
    ("میخام اسپرسوساز بگیرم، چی خوبه؟", "buy"),
    ("اگه اسپرسوساز تمیز دارید خریدارم", "buy"),
    ("فروش دستگاه اسپرسوساز صنعتی با ضمانت", "sell"),
    ("اسپرسوساز موجوده، ارسال به شهرستان", "sell"),
    ("فروشنده عمده دستگاه اسپرسوساز هستیم", "sell"),
    ("دستگاه اسپرسوساز برای فروش دارم", "sell"),
    ("اسپرسوساز نو با تخفیف ویژه و ارسال داریم", "sell"),
    ("فروشگاه اسپرسوساز هستیم، قیمت همکاری", "sell"),
    ("دستگاه اسپرسوساز کارکرده موجود است", "sell"),
    ("نمایندگی فروش اسپرسوساز در تهران", "sell"),
    ("قیمت دستگاه اسپرسوساز چنده؟", "question"),
    ("برای خرید اسپرسوساز چه مدلی پیشنهاد می‌کنید؟", "question"),
    ("کسی قیمت اسپرسوساز صنعتی رو می‌دونه؟", "question"),
    ("اسپرسوساز خوب از کجا سراغ دارید؟", "question"),
    ("تفاوت مدل‌های دستگاه اسپرسوساز چیه؟", "question"),
    ("امروز هوا خیلی خوبه", "irrelevant"),
    ("برای کافه دنبال میز چوبی هستم", "irrelevant"),
    ("قهوه تازه‌برشت برای کافه می‌فروشیم", "irrelevant"),
    ("دستگاه اسپرسوساز دارم و می‌خوام بفروشم", "sell"),
    ("هم دستگاه اسپرسوساز می‌فروشم هم برای کافه یکی دیگه می‌خرم", "buy"),
    ("اسپرسوساز دو گروپ برای کافه لازم دارم", "buy"),
    ("می‌خواستم درباره قیمت اسپرسوساز سوال کنم", "question"),
    ("اسپرسوساز صنعتی، فروش عمده مستقیم از واردکننده", "sell"),
]


def evaluate_persian_rules() -> dict[str, object]:
    profile = ProductProfile("دستگاه اسپرسوساز")
    labels = {"buy", "sell", "question", "irrelevant"}
    matrix = {label: {predicted: 0 for predicted in sorted(labels)} for label in sorted(labels)}
    errors = []
    for index, (text, expected) in enumerate(PERSIAN_CASES, start=1):
        actual = local_classify(SourceRecord("evaluation", str(index), text), profile).intent
        actual = actual if actual in labels else "irrelevant"
        matrix[expected][actual] += 1
        if expected != actual:
            errors.append({"text": text, "expected": expected, "actual": actual})
    per_class = {}
    for label in sorted(labels):
        true_positive = matrix[label][label]
        false_positive = sum(matrix[other][label] for other in labels if other != label)
        false_negative = sum(matrix[label][other] for other in labels if other != label)
        precision = true_positive / (true_positive + false_positive) if true_positive + false_positive else 0.0
        recall = true_positive / (true_positive + false_negative) if true_positive + false_negative else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per_class[label] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4), "support": sum(matrix[label].values())}
    accuracy = sum(matrix[label][label] for label in labels) / len(PERSIAN_CASES)
    macro_f1 = sum(values["f1"] for values in per_class.values()) / len(per_class)
    return {"examples": len(PERSIAN_CASES), "accuracy": round(accuracy, 4), "macro_f1": round(macro_f1, 4), "per_class": per_class, "confusion_matrix": matrix, "errors": errors}
