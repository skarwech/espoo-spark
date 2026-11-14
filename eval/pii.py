"""Regex PII scanner: emails (non-reserved domains), phone numbers, Finnish personal IDs, IBANs.

ponytail: regex only, no NER model. Names are covered separately by checking each story
against its owner's identity record (test_f0). Add NER when free text comes from real users.
"""
import re

RESERVED_EMAIL = re.compile(r"@([\w-]+\.)*(example\.(com|org|net)|[\w-]+\.(test|example|invalid|localhost))$", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w+])((?:\+|00)\d{1,3}[ -]?\(?\d{1,4}\)?([ -]?\d{2,4}){2,4}"  # international
                   r"|\b0[45]\d[ -]?\d{3}[ -]?\d{3,4}"  # Finnish mobile, with or without spaces
                   r"|\b0\d{1,2}[ -]\d{3}[ -]?\d{3,4})\b")  # Finnish landline, e.g. 09 123 4567
HETU = re.compile(r"\b(\d{6})([-+ABCDEFYXWVU])(\d{3})([0-9A-Y])\b")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}(?: ?[A-Z0-9]{1,4})?\b")
HETU_CHECK = "0123456789ABCDEFHJKLMNPRSTUVWXY"


def _valid_hetu(m: re.Match) -> bool:
    return HETU_CHECK[int(m.group(1) + m.group(3)) % 31] == m.group(4)


def scan(text: str) -> list[tuple[str, str]]:
    """Return (kind, match) for every PII-looking span in text."""
    hits = [("email", m.group()) for m in EMAIL.finditer(text) if not RESERVED_EMAIL.search(m.group())]
    hits += [("phone", m.group()) for m in PHONE.finditer(text)]
    hits += [("hetu", m.group()) for m in HETU.finditer(text) if _valid_hetu(m)]
    hits += [("iban", m.group()) for m in IBAN.finditer(text)]
    return hits


def walk_strings(obj):
    """Yield every string inside nested dicts/lists."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from walk_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from walk_strings(v)


SCHOOL_GENERIC = {"university", "universities", "of", "the", "applied", "sciences", "school", "economics", "college", "vocational",
                  "institute", "technology", "polytechnic", "national", "state", "federal", "royal", "middle", "east", "and",
                  "science", "world", "economy"}


# Nationality forms in en / fi / sv as word-start stems (Finnish case endings follow the stem; consonant-gradation stems are listed
# separately), and in Arabic as whole words after optional clitics (و ف ب ك ل, the article ال, the contraction لل) with optional
# plural / feminine suffixes; Arabic diacritics and tatweel are stripped before matching. Stems avoid common words (no bare "turk": Turku;
# no "syri": syringe; no "kines": kinesiology; "india" not before "na"). Known homographs remain and fail safe (a spurious red gate):
# Arabic المغرب is also "sunset prayer", صينية also "tray", هند also a first name.
_AR = r"(?<![؀-ۿ])(?:[وف]?(?:لل|[بكل]?ال|[بكل])?)(?:{})(?:ين|ون|ية|يين|يون|يات|يا|ي|ا)?(?![؀-ۿ])"
_AR_MARKS = re.compile(r"[ً-ْـ]")
NATIONALITY_FORMS = {
    "Syrian": (r"syria", r"syrie[nr]", r"syrisk", r"syyria", _AR.format("سوري|سوريا|سورية")),
    "Iraqi": (r"iraq", r"irak", _AR.format("عراق|عراقي")),
    "Egyptian": (r"egypt", _AR.format("مصر|مصري")),
    "Moroccan": (r"morocc", r"marokk", r"marok(?:o|on|ossa|osta|olle|olla)\b", r"marock", _AR.format("مغرب|مغربي|مغاربة")),
    "Estonian": (r"estonia", r"viro(?:lai|ssa|sta|n\b|on\b|a\b|lle\b|lla\b|lta\b|\b)", r"eesti", r"estn", r"estland", _AR.format("إستوني|إستونيا|استوني|استونيا")),
    "Indian": (r"india(?!na)", r"intia(?!ani)", r"indisk", r"indien", _AR.format("هند|هندي|هنود")),
    "Nigerian": (r"nigeria", _AR.format("نيجيري|نيجيريا")),
    "Ukrainian": (r"ukrain", _AR.format("أوكراني|أوكرانيا|اوكراني|اوكرانيا")),
    "Vietnamese": (r"vietnam", _AR.format("فيتنام|فيتنامي")),
    "Brazilian": (r"brazil", r"brasil", _AR.format("برازيل|برازيلي")),
    "Chinese": (r"china\b", r"chinese", r"kiina", r"kinesisk", r"kineser", r"kina[s]?\b", _AR.format("صين|صيني")),
    "Filipino": (r"filipin", r"philippin", r"filippiin", r"filippin", _AR.format("فلبين|فلبيني")),
    "Iranian": (r"iran", _AR.format("إيران|إيراني|ايران|ايراني")),
    "Turkish": (r"turkish", r"turkey", r"turkki", r"turki(?:ssa|sta|n\b|in\b|lle\b|lla\b|lta\b)", r"turkisk", r"turkiet", r"türk", _AR.format("ترك|تركي|تركيا")),
    "Nepali": (r"nepal", _AR.format("نيبال|نيبالي")),
    "Bulgarian": (r"bulgari", r"bulgarisk", r"bulgarien", _AR.format("بلغار|بلغاري|بلغاريا")),
}
# Finnish case endings for names (explicit list: a bare prefix would turn "Aino" into "ainoa" = "only", "Saari" into "saaristo").
_FI_CASE = r"(?:n|ssa|ssä|sta|stä|lla|llä|lta|ltä|lle|ksi|na|nä|in|iin|aan|ään|een|lta|t)?"


def nationality_hits(text: str, nationality: str) -> list[str]:
    """Matches of any en/fi/sv/ar form of a nationality (Finnish and Swedish are language names, so never flagged)."""
    if nationality in ("Finnish", "Swedish"):
        return []
    forms = NATIONALITY_FORMS.get(nationality, (re.escape(nationality.lower()),))
    plain = _AR_MARKS.sub("", text)
    return [m.group() for f in forms for m in re.finditer(f if f.startswith("(?<!") else rf"\b{f}\w*", plain, re.I)]


def identity_leaks(text: str, ident: dict) -> list[str]:
    """Identity strings found in text: each name part as a whole word or with a Finnish case ending ("Kovalenkon", "Karimin");
    nationality in en/fi/sv/ar; distinctive school tokens (case-sensitive prefix, so "Omniassa" or "Aaltolla" are caught).
    Known limits: names in Arabic script are not transliterated; translated city or school names (Tarto-, Kiova-, Damasko-) are not listed."""
    hits = [t for t in ident["name"].split() if re.search(rf"\b{re.escape(t)}{_FI_CASE}\b", text, re.I)]
    hits += nationality_hits(text, ident["nationality"])
    tokens = [t for t in re.findall(r"[\w']+", ident["school"]) if len(t) >= 4 and t.lower() not in SCHOOL_GENERIC]
    hits += [t for t in tokens if re.search(rf"\b{re.escape(t)}", text)]
    return hits
