"""
Translated (human-drafted, not live-translated) text for non-English
agent/risk output.

WHY THIS IS A SEPARATE MODULE, NOT FIELDS ON THE DOMAIN REGISTRY
------------------------------------------------------------------
app.core.domains is deliberately kept as "one entry per domain, nothing
else" (see its own module docstring). Translations are looked up here,
keyed by the same domain_id / signal field / countermeasure_id already
used everywhere else, so adding a language never touches the domain
registry's shape and adding a domain never requires touching this file
unless you also want to translate it.

WHY THESE ARE STATIC DICTS, NOT A LIVE TRANSLATION CALL
------------------------------------------------------------------
Every string here is fixed and human-drafted ahead of time, exactly like
the English templates in app.core.domains -- never translated on the fly
at request time. This preserves the same guarantee the rest of the
safety design relies on (see app.core.agent's module docstring): no
code path where freshly-generated text (from a translation API or an
LLM) can end up in a clinical-sounding sentence.

CURRENT COVERAGE: Russian ("ru") only, chosen first given ISS crew
composition. The English string is always the fallback when a lang code
or a specific key isn't present below -- translate() never raises and
never mixes languages mid-sentence.

TRANSLATION QUALITY NOTE: drafted in this session, not yet reviewed by a
native/qualified Russian speaker. Treat as a first pass -- get a native
speaker to review before this is used with anyone who isn't just kicking
the tires on the demo.
"""

from __future__ import annotations

DEFAULT_LANG = "en"
SUPPORTED_LANGS = {"en", "ru"}


def normalize_lang(lang: str | None) -> str:
    if not lang:
        return DEFAULT_LANG
    code = lang.split("-")[0].lower()
    return code if code in SUPPORTED_LANGS else DEFAULT_LANG


# --- Domain display names --------------------------------------------------
DOMAIN_DISPLAY_NAMES: dict[str, dict[str, str]] = {
    "sans": {"ru": "SANS (нейроокулярный синдром)"},
    "immune_dysregulation": {"ru": "Иммунная дисрегуляция"},
}

# --- Domain narrative templates (still take .format(window=...)) ----------
DOMAIN_NARRATIVES: dict[str, dict[str, dict[str, str]]] = {
    "sans": {
        "elevated": {
            "ru": (
                "Показатели зрения и сердечно-сосудистой системы за последние {window} дней "
                "указывают на повышенный и нарастающий риск, связанный с SANS. Это не диагноз "
                "-- обсудите с врачом экипажа и рассмотрите повторную визуализацию."
            )
        },
        "stable": {
            "ru": (
                "Показатели зрения и сердечно-сосудистой системы за последние {window} дней "
                "остаются в пределах ожидаемого диапазона риска, связанного с SANS, для этого "
                "члена экипажа."
            )
        },
    },
    "immune_dysregulation": {
        "elevated": {
            "ru": (
                "Показатели вирусной нагрузки и гормонов стресса за последние {window} дней "
                "указывают на повышенный риск иммунной дисрегуляции. Это не диагноз -- "
                "рекомендуется анализ сна и уровня стресса, а также наблюдение за симптомами."
            )
        },
        "stable": {
            "ru": (
                "Показатели вирусной нагрузки и гормонов стресса за последние {window} дней "
                "остаются в пределах ожидаемого диапазона для этого члена экипажа."
            )
        },
    },
}

NOT_IMPLEMENTED_NARRATIVE_I18N: dict[str, str] = {
    "ru": (
        "Архитектура готова -- синтетические сигналы для этого направления пока не "
        "реализованы. Это временная заглушка в реестре платформы, а не клинический результат."
    )
}

FALLBACK_NARRATIVE_I18N: dict[str, str] = {
    "ru": (
        "Недостаточно данных за последнее время для достоверной оценки риска у этого члена "
        "экипажа. Рекомендуется собрать дополнительные измерения, прежде чем делать выводы."
    )
}

# --- Signal labels (used in "top contributors" and countermeasure rationale) --
SIGNAL_LABELS: dict[str, dict[str, str]] = {
    "onsd_mm": {"ru": "диаметр оболочки зрительного нерва"},
    "globe_flattening_index": {"ru": "уплощение глазного яблока"},
    "hrv_ms": {"ru": "вариабельность сердечного ритма"},
    "radiation_dose_msv": {"ru": "накопленная доза радиации"},
    "ebv_shedding_copies_ml": {"ru": "выделение вируса Эпштейна-Барр"},
    "cortisol_dhea_ratio": {"ru": "соотношение кортизол:ДГЭА"},
}

# --- Countermeasures ---------------------------------------------------------
COUNTERMEASURE_TEXT: dict[str, dict[str, dict[str, str]]] = {
    "lbnp-protocol": {
        "name": {"ru": "Сеансы отрицательного давления на нижнюю часть тела (LBNP)"},
        "description": {
            "ru": (
                "Механическая контрмера, проходящая оценку переносимости для SANS; "
                "перераспределяет цефалический сдвиг жидкости во время запланированных сеансов."
            )
        },
    },
    "exercise-adjust": {
        "name": {"ru": "Корректировка протокола тренировок"},
        "description": {"ru": "Скорректируйте баланс силовых и кардионагрузок по указанию врача экипажа."},
    },
    "sleep-stress-review": {
        "name": {"ru": "Анализ сна и уровня стресса"},
        "description": {
            "ru": "Структурированный анализ дефицита сна и психологической нагрузки совместно с главным врачом миссии."
        },
    },
    "clinical-referral": {
        "name": {"ru": "Направление на асинхронный обзор врачом-хирургом полёта"},
        "description": {"ru": "Бортовая фармакологическая контрмера не установлена; требуется эскалация для рассмотрения."},
    },
}

COUNTERMEASURE_RATIONALE_TEMPLATE_I18N: dict[str, str] = {
    "ru": (
        "Предложено, поскольку текущий риск составляет {risk:.0%} и растёт "
        "({delta:+.0%} за 30 дней), в первую очередь из-за: {contributors}. Оператор должен "
        "рассмотреть это предложение и принять решение о его применении."
    )
}
MULTIPLE_SIGNALS_I18N: dict[str, str] = {"ru": "нескольких показателей"}

# --- Agent opening tones ------------------------------------------------------
OPENING_TONES_I18N: dict[str, dict[str, str]] = {
    "all_nominal": {
        "ru": "Все активные показатели здоровья этого члена экипажа в настоящее время находятся в пределах ожидаемого диапазона."
    },
    "one_or_more_elevated": {
        "ru": "Один или несколько активных показателей здоровья этого члена экипажа требуют дополнительного рассмотрения."
    },
    "insufficient_data": {
        "ru": "Пока недостаточно данных за последнее время для составления достоверной сводки по этому члену экипажа."
    },
}

# --- Agent render_entry glue words -------------------------------------------
GLUE_I18N: dict[str, dict[str, str]] = {
    "current_risk": {"ru": "текущий риск"},
    "confidence": {"ru": "достоверность"},
    "day_suffix": {"ru": "дн"},
    "suggested": {"ru": "Предложено"},
}

DISCLAIMER_I18N: dict[str, str] = {
    "ru": "\n\n_Это средство поддержки принятия решений, а не диагноз. Любое действие должен подтвердить человек-эксперт._"
}


def translate(table: dict[str, str], lang: str, fallback: str) -> str:
    """Look up `lang` in a flat {lang: text} dict, falling back to
    `fallback` (the caller's English default) if missing. Never raises,
    never mixes languages."""
    if lang == DEFAULT_LANG:
        return fallback
    return table.get(lang, fallback)
