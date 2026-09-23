from .market import stamp, RIYADH

SESSIONS = {"pre": "قبل السوق", "regular": "الجلسة الرئيسية", "after": "بعد السوق", "overnight": "الجلسة الليلية"}


def entry(c):
    score = "غير مفعّل — نجمع بيانات الاختبار" if c.ai_score is None else f"{c.ai_score:.2f} (درجة نموذج، ليست نسبة نجاح مضمونة)"
    label = "🧪 فرصة شراء تجريبية" if c.mode == "paper" else "🔔 فرصة شراء للمراجعة"
    return (f"{label} | {c.symbol}\n"
            f"{SESSIONS[c.session]} • البيانات: {c.feed.upper()}\n"
            f"السعر المرجعي: ${c.entry:.2f}\nنطاق الدخول: ${c.entry:.2f}–${c.entry_max:.2f}\n"
            f"الهدف الأول: ${c.target1:.2f}\nالهدف النهائي: ${c.target2:.2f}\nوقف الخسارة: ${c.stop:.2f}\n"
            f"حجم افتراضي: {c.shares} سهم • مخاطرة سعرية تقريبية: ${c.risk_usd:.2f}\n"
            f"السبب: اختراق قمة 20 دقيقة، اتجاه صاعد، توسع حجم التداول، فوق VWAP لآخر 90 دقيقة بحد أقصى.\n"
            f"الذكاء الاصطناعي: {score}\n"
            f"وقت الرصد بالرياض: {stamp(c.created).astimezone(RIYADH):%Y-%m-%d %H:%M:%S}\n"
            f"صلاحية الدخول: 60 ثانية من وقت الرصد، ويلغى عند تجاوز النطاق أو الوقف.\n"
            f"نهاية المتابعة: {stamp(c.expires).astimezone(RIYADH):%H:%M} الرياض.\n"
            "تنبيه فقط؛ لا يُنفّذ أمرًا ولا يضمن تعبئة أو ربحًا. الوقف قد يُتجاوز مع الفجوات.")


def update(data, kind, price, now):
    labels = {"target1": "رُصد بلوغ الهدف الأول (تستمر المتابعة دون افتراض بيع جزئي)",
              "target2": "رُصد بلوغ الهدف النهائي — انتهت المتابعة",
              "stop": "رُصد تجاوز وقف الخسارة — انتهت المتابعة",
              "timeout": "انتهى وقت المتابعة — راجع الخروج",
              "unknown": "انتهت المتابعة بنتيجة غير معروفة بسبب فجوة في البيانات"}
    quote = "غير متاح" if price is None else f"${price:.2f}"
    return (f"{'🧪 متابعة تجريبية' if data['mode'] == 'paper' else '🔔 متابعة تنبيه'} | {data['symbol']}\n"
            f"{labels[kind]}\nآخر سعر بيع مرصود: {quote}\n"
            f"الوقت بالرياض: {now.astimezone(RIYADH):%Y-%m-%d %H:%M:%S}\n"
            "هذه متابعة أسعار مرصودة؛ ليست تأكيد تنفيذ شراء أو بيع.")
