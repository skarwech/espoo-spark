"""Identifier warnings for Skill Stories (F3): names, contacts, birthdates, nationality statements, school names, in fi/en/sv.
Warnings only: the person decides; generic wording is offered. Built from general patterns, not from the test fixture."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from pii import NATIONALITY_FORMS, nationality_hits, scan  # noqa: E402

SUGGEST = {"email": "[contact removed]", "phone": "[contact removed]", "hetu": "[remove]", "iban": "[remove]",
           "birthdate": "remove the date or age", "nationality": "an international professional", "name": "a colleague / I",
           "school": "a university / a vocational school"}
COUNTRIES = (r"Syria|Iraq|Egypt|Morocco|Estonia|India|Nigeria|Ukraine|Vietnam|Brazil|China|Philippines|Iran|Turkey|Türkiye|Nepal|"
             r"Bulgaria|Russia|Somalia|Afghanistan|Pakistan|Bangladesh|Poland|Romania|Spain|Italy|France|Germany|Kenya|Ethiopia|Sri Lanka|"
             r"Thailand|Indonesia|Mexico|Colombia|Peru|Argentina|Lebanon|Jordan|Sudan|Eritrea|Greece|Portugal|Latvia|Lithuania|Belarus")
DEMONYMS = (r"Russian|Somali|Afghan|Pakistani|Bangladeshi|Polish|Romanian|Spanish|Italian|French|German|Kenyan|Ethiopian|Sri Lankan|"
            r"Thai|Indonesian|Mexican|Colombian|Peruvian|Argentinian|Lebanese|Jordanian|Sudanese|Eritrean|Greek|Portuguese|Latvian|"
            r"Lithuanian|Belarusian|venäläi\w*|somali\w*|afgaani\w*|pakistanilai\w*|puolalai\w*|romanialai\w*|espanjalai\w*|"
            r"italialai\w*|ranskalai\w*|saksalai\w*|kenialai\w*|etiopialai\w*|kreikkalai\w*|latvialai\w*|liettualai\w*")
PATTERNS = {
    "birthdate": [r"\b\d{1,2}\s?[./-]\s?\d{1,2}\s?[./-]\s?(?:19|20)\d{2}\b", r"\b(?:19|20)\d{2}-\d{2}-\d{2}\b",
                  r"\bborn\b(?:\s+\w+){0,3}?\s+(?:in\s+|on\s+)?(?:19|20)\d{2}\b", r"\b(?:syntynyt|synnyin|född|föddes)\b[^.]{0,30}?\b(?:19|20)\d{2}\b",
                  r"\b\d{2}[- ]year[- ]old\b", r"\bage(?:d)? \d{2}\b", r"\b\d{2}[- ]?vuotia\w*", r"\b\d{2} vuoden ikäi\w*", r"\b\d{2} år gammal\b",
                  r"\bvuonna (?:19|20)\d{2} synty\w*", r"\b(?:I am|I'm) \d{2}\b"],
    "nationality": [rf"\b(?:I am|I'm|as an?|being an?)\s+(?:{DEMONYMS})\b", rf"\b(?:from|grew up in|born in|moved here from)\s+(?:{COUNTRIES})\b",
                    r"\bkotoisin\s+\w+", r"\b(?:kansalai\w*|nationality|citizenship|kansalaisuus\w*|medborgar\w*)\b",
                    rf"\b(?:{DEMONYMS})\b", r"\b(?:olen|minä olen)\s+\w+lai(?:nen|sena)\b", r"\bkommer från\s+\w+"],
    "school": [r"\bUniversity of [A-Z][\w-]+(?: [A-Z][\w-]+)?", r"\b[A-Z][\w-]+(?: [A-Z][\w-]+)? (?:University|College|Polytechnic|Institute|Academy)\b",
               r"\b[A-ZÅÄÖ][\w-]*-?(?:yliopisto|korkeakoulu|ammattikorkeakoulu|opisto|lukio)\w*", r"\b(?:yliopisto|korkeakoulu|universitet|högskola)\w*\s+[A-ZÅÄÖ]\w+",
               r"\b[A-ZÅÄÖ]\w+ (?:yliopisto|ammattikorkeakoulu|opisto|lukio|universitet|högskola)\w*",
               r"\b(?:Aalto|Metropolia|Haaga-Helia|Laurea|Omnia|Stadin|Arcada|Hanken|Tampereen|Turun|Oulun|LUT|Novia|Xamk|Savonia|Karelia|Lapin|"
               r"Jyväskylän|Vaasan|Helsingin|Itä-Suomen|Åbo Akademi|Business College|Varia|Careeria|Keuda|Sedu|Hyria|Taitotalo)\w*", r"\bAMK\b"],
    "name": [r"\b(?i:my name is|I am called|nimeni on|jag heter|mitt namn är)\s+[A-ZÅÄÖ][\w'-]+(?:\s+[A-ZÅÄÖ][\w'-]+)?",
             r"\b(?i:I'm|I am|olen|minä olen|jag är)\s+[A-ZÅÄÖ][a-zåäö'-]+\s+[A-ZÅÄÖ][a-zåäö'-]+",
             r"\b(?i:manager|colleague|boss|supervisor|teammate|mentor|lead|director|CEO|esimie\w*|kollega\w*|pomo\w*|tiimikaveri\w*|"
             r"työkaveri\w*|kollegan|chef|handledare)\s+[A-ZÅÄÖ][a-zåäö'-]+(?:\s+[A-ZÅÄÖ][a-zåäö'-]+)?",
             r"\b(?i:together with|with|med)\s+[A-ZÅÄÖ][a-zåäö'-]+\s+[A-ZÅÄÖ][a-zåäö'-]+",
             r"\b[A-ZÅÄÖ][a-zåäö'-]+\s+[A-ZÅÄÖ][a-zåäö'-]+(?:n|in|lle|lta|n kanssa)\s+(?:kanssa|kanssani|tiimissä|johdolla)\b"],
}
NOT_NAMES = {"Power", "Excel", "Google", "Microsoft", "Teams", "Python", "Jira", "Slack", "Azure", "Amazon", "Sheets", "Power BI", "SAP",
             "Espoo", "Helsinki", "Finland", "Suomi", "Vantaa", "Tampere", "Turku", "Oulu", "Monday", "January", "Kela", "Te"}
COMPILED = {cat: [re.compile(p) if cat in ("school", "name") else re.compile(p, re.I) for p in ps] for cat, ps in PATTERNS.items()}


def warnings(text: str) -> list[dict]:
    """[{category, match, start, end, suggestion}] for every identifier-looking span, deduplicated, in text order."""
    found = [(k, m.group(), m.start(), m.end()) for k, m_text in scan(text) for m in [re.search(re.escape(m_text), text)] if m]
    found = [("phone" if k == "phone" else "email" if k == "email" else k, s, a, b) for k, s, a, b in found]
    for cat, pats in COMPILED.items():
        for p in pats:
            for m in p.finditer(text):
                if cat == "name" and any(w in NOT_NAMES for w in m.group().split()[-2:]):
                    continue
                found.append((cat, m.group(), m.start(), m.end()))
    found += [("nationality", h, text.find(h), text.find(h) + len(h)) for n in NATIONALITY_FORMS for h in nationality_hits(text, n)
              if text.find(h) >= 0]
    out, taken = [], []
    for cat, s, a, b in sorted(found, key=lambda x: (x[2], -(x[3] - x[2]))):
        if any(a < tb and ta < b for ta, tb in taken):  # overlapping spans: keep the first, longest
            continue
        taken.append((a, b))
        out.append({"category": cat, "match": s, "start": a, "end": b, "suggestion": SUGGEST.get(cat, "[remove]")})
    return out
