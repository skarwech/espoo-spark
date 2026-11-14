"""Seeded synthetic world. Usage: python data/generate.py [--seed 42] [--out data/out]

Composes only from frozen files (bank/, heroes.json, common.json, archetypes.json, esco_cache.json).
No network, no LLM, no timestamps: the same seed gives byte-identical output.
Quotas are pre-registered in docs/method.md §2.
"""
import argparse
import json
import random
import unicodedata
from pathlib import Path

HERE = Path(__file__).parent
LANG_QUOTA = {"en": 60, "fi": 45, "sv": 23, "ar": 22}
BG_QUOTA = {"international_professional": 45, "local_graduate": 30, "career_changer": 30, "returner": 23, "vocational": 22}
STYLE_QUOTA = {"polished": 75, "rough": 75}
ORIGIN_QUOTA = {"iot": 32, "sw": 24, "data": 22, "log": 14, "care": 14, "cs": 16, "mfg": 14, "fin": 14}  # tech-heavy, like Espoo
NEED_QUOTA = {"iot": 4, "sw": 5, "data": 3, "log": 2, "care": 3, "cs": 3, "mfg": 2, "fin": 2}  # + hero NordSense
N_GENERIC, N_THIN = 12, 5
LEVEL_WEIGHTS = {"supported": 45, "partial": 35, "none": 20}
CHANGE_TO = {"log": ["data", "sw"], "care": ["data", "cs"], "cs": ["sw", "data"], "mfg": ["iot", "data"],
             "fin": ["data", "sw"], "iot": ["sw", "data"], "sw": ["data", "iot"], "data": ["sw", "iot"]}
LOCAL_BG = {"local_graduate", "vocational", "returner"}
STARTS = ["2026-12", "2027-01", "2027-02", "2027-03", "2027-04", "2027-05"]
PRIORITY_RANK = {"must": 0, "learnable": 1, "nice": 2}
JOB_LANGS = ("en", "fi", "sv")  # working languages; a "native" level would map 1:1 to national origin


def read(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def job_languages(langs: list[dict]) -> list[dict]:
    """Keep working languages only, native -> C2. Applied after RNG draws so stories stay byte-identical."""
    return [{"lang": l["lang"], "level": "C2" if l["level"] == "native" else l["level"]} for l in langs if l["lang"] in JOB_LANGS]


def quota_list(quota: dict, used: dict, rng: random.Random) -> list:
    items = [k for k, n in quota.items() for _ in range(n - used.get(k, 0))]
    rng.shuffle(items)
    return items


def ascii_fold(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower().replace(" ", "").replace("-", "")


class World:
    def __init__(self, seed: int):
        self.seed = seed
        self.arch = read("archetypes.json")
        self.common = read("common.json")
        self.heroes = read("heroes.json")
        self.bank = {d: read(f"bank/{d}.json") for d in self.arch["domains"]}
        self.esco = {s["label"].lower(): uri for uri, s in read("esco_cache.json")["skills"].items()}
        self.archetypes = {a["id"]: a for a in self.arch["archetypes"]}
        self.by_domain = {d: [a["id"] for a in self.arch["archetypes"] if a["domain"] == d] for d in self.arch["domains"]}
        self.claims = {c["id"]: c for b in self.bank.values() for c in b["claims"]}
        self.related = {}
        for a, b in self.arch["related"]:
            self.related.setdefault(a, set()).add(b)
            self.related.setdefault(b, set()).add(a)

    def rng(self, stream: str) -> random.Random:
        return random.Random(f"{self.seed}:{stream}")  # str seeds hash via sha512: stable across runs

    # ---------- jobseekers ----------
    def jobseekers(self):
        heroes = self.heroes["candidates"]

        def used(key):  # heroes consume part of each quota
            vals = [h["story"]["lang"] if key == "lang" else h["attributes"][key] for h in heroes]
            return {v: vals.count(v) for v in vals}

        langs = quota_list(LANG_QUOTA, used("lang"), self.rng("lang"))
        bgs = quota_list(BG_QUOTA, used("background"), self.rng("background"))
        styles = quota_list(STYLE_QUOTA, used("style"), self.rng("style"))
        origins = quota_list(ORIGIN_QUOTA, used("origin_domain"), self.rng("origin"))
        n = len(langs)
        planted = self.rng("planted").sample(range(n), N_GENERIC + N_THIN)
        tags = {i: ["generic"] for i in planted[:N_GENERIC]} | {i: ["thin"] for i in planted[N_GENERIC:]}
        people = [self._hero(h) for h in heroes]
        for i in range(n):
            people.append(self._person(f"J-{i + 4:03d}", langs[i], bgs[i], styles[i], origins[i], tags.get(i, []), self.rng(f"person:{i}")))
        return people

    def _hero(self, h):
        js = {k: h[k] for k in ("candidate_id", "story", "experience_industry")}
        js["constraints"] = {**h["constraints"], "languages": job_languages(h["constraints"]["languages"])}
        return {"jobseeker": js,
                "identity": {"candidate_id": h["candidate_id"], **h["identity"]},
                "attributes": {"candidate_id": h["candidate_id"], **h["attributes"]},
                "claims": h["planted_claims"], "items": None}

    def _person(self, cid, lang, bg, style, origin, tags, rng):
        local_ok = bg in LOCAL_BG
        groups = [g for g in self.common["groups"] if lang in g["langs"] and
                  (g["local"] if local_ok else (not g["local"]) if bg == "international_professional" else True)]
        group = rng.choice(groups)
        target = rng.choice(CHANGE_TO[origin]) if bg == "career_changer" else origin
        thin, generic = "thin" in tags, "generic" in tags
        archs = rng.sample(self.by_domain[origin], 1 if thin else rng.choices([2, 3], weights=[40, 60])[0])
        claims = []
        for a in archs:
            level = "none" if thin or generic else rng.choices(list(LEVEL_WEIGHTS), weights=list(LEVEL_WEIGHTS.values()))[0]
            claims.append({"claim_id": f"{a}.{level}.{rng.choice([1, 2])}", "archetype": a, "level": level})
        bank = self.bank[origin]
        mode = rng.choices(["onsite", "hybrid", "remote"], weights=[30, 50, 20])[0]
        ctx_item = rng.choice(bank["contexts"])  # draw order matters: context, differently, next, used_ai
        diff_item = None if thin else rng.choice(bank["differently"])
        next_item = rng.choice(self.bank[target]["next"])
        items = {"problem": ctx_item["id"], "differently": diff_item and diff_item["id"], "next": next_item["id"], "mode": mode}
        story = {"lang": lang, **self.compose(claims, items, lang, style), "used_ai": rng.random() < 0.3}
        constraints = {"languages": job_languages(self._languages(group, lang, local_ok, rng)), "start": rng.choice(STARTS),
                       "work_mode": mode, "needs_relocation": (not group["local"]) and rng.random() < 0.25}
        first, last = rng.choice(group["first"]), rng.choice(group["last"])
        vocational = [s for s in group["schools"] if "vocational" in s]
        school = rng.choice(vocational) if bg == "vocational" and vocational else rng.choice(group["schools"])
        education = ("vocational" if bg == "vocational" else
                     rng.choice(["university", "applied_sciences"]) if bg == "local_graduate" else
                     rng.choices(["university", "applied_sciences", "vocational", "self_taught"], weights=[40, 25, 15, 20])[0])
        birth = rng.randint(1996, 2003) if bg == "local_graduate" else rng.randint(1970, 1990) if bg == "returner" else rng.randint(1968, 2000)
        ctx = self.common["private_context"][lang]
        return {
            "jobseeker": {"candidate_id": cid, "story": story, "constraints": constraints,
                          "experience_industry": self.arch["domains"][origin]["industry"]},
            "identity": {"candidate_id": cid, "name": f"{first} {last}", "nationality": group["nationality"], "school": school,
                         "birth_year": birth, "email": f"{ascii_fold(first)}.{ascii_fold(last)[0]}.{cid[2:]}@example.test",
                         "private_context": rng.choice(ctx) if rng.random() < 0.3 else None},
            "attributes": {"candidate_id": cid, "background": bg, "education": education,
                           "career_gap": bg == "returner" or rng.random() < 0.1, "style": style,
                           "origin_domain": origin, "target_domain": target, "planted_tags": tags},
            "claims": claims,
            "items": items,
        }

    def compose(self, claims: list[dict], items: dict, lang: str, style: str) -> dict:
        """Story boxes from bank item ids. Also used to build translation and style twins of a pool story."""
        by_id = {it["id"]: it for b in self.bank.values() for k in ("contexts", "differently", "next") for it in b[k]}
        text = lambda item_id: by_id[item_id]["text"][lang][style]
        return {"problem": text(items["problem"]),
                "did": " ".join(self.claims[c["claim_id"]]["text"][lang][style] for c in claims),
                "differently": text(items["differently"]) if items["differently"] else "",
                "next": text(items["next"]) + " " + self.common["work_mode"][items["mode"]][lang][style]}

    @staticmethod
    def _languages(group, story_lang, local, rng):
        out = [{"lang": group["native"], "level": "native"}]
        have = lambda l: any(x["lang"] == l for x in out)
        if not have(story_lang):
            out.append({"lang": story_lang, "level": rng.choice(["B2", "C1", "C2"])})
        if not have("en"):
            out.append({"lang": "en", "level": rng.choice(["B1", "B2", "C1", "C2"])})
        if not have("fi"):
            out.append({"lang": "fi", "level": rng.choice(["B2", "C1", "C2"] if local else ["A1", "A1", "A2", "B1", "B2"])})
        return out

    # ---------- companies and needs ----------
    def needs(self):
        rng = self.rng("needs")
        names = list(self.common["company_names"])
        rng.shuffle(names)
        domains = [d for d, n in NEED_QUOTA.items() for _ in range(n)]
        rng.shuffle(domains)
        ids = [i for i in range(1, 26) if i != 14]
        companies, needs, outcome_truth = [], [], {}
        for num, dom, name in zip(ids, domains, names):
            c, n, truth = self._need(num, dom, name, rng)
            companies.append(c)
            needs.append(n)
            outcome_truth[n["need_id"]] = truth
        hc, hn = self.heroes["company"], dict(self.heroes["need"])
        outcome_truth[hn["need_id"]] = hn.pop("planted_archetypes")
        hn["outcomes"] = [{**{k: v for k, v in o.items() if k != "skills_by_label"},
                           "skills": [self.esco[l.lower()] for l in o["skills_by_label"]]} for o in hn["outcomes"]]
        hn["skills"] = [{"esco_uri": self.esco[l.lower()], "label": l, "level": lvl} for l, lvl in hn.pop("skills_by_label")]
        return sorted(companies + [hc], key=lambda x: x["company_id"]), sorted(needs + [hn], key=lambda x: x["need_id"]), outcome_truth

    def _need(self, num, dom, name, rng):
        musts = rng.sample(self.by_domain[dom], 2)
        third = next(a for a in self.by_domain[dom] if a not in musts)
        rel = sorted((self.related.get(musts[0], set()) | self.related.get(musts[1], set())) - set(musts))
        r = rng.random()
        extra = ((third, rng.choice(["learnable", "nice"])) if r < 0.5 or (r < 0.8 and not rel) else
                 (rng.choice(rel), "nice") if r < 0.8 else
                 ("cs_support", "learnable") if dom != "cs" else (third, "learnable"))
        lang = "fi" if rng.random() < (0.6 if dom == "care" else 0.3) else "en"
        picked = [(a, "must") for a in musts] + [extra]
        outcomes = [{"id": f"o{i + 1}", "text": self.archetypes[a]["outcome"][lang], "priority": p,
                     "skills": [self.esco[l.lower()] for l in self.archetypes[a]["skills"]]} for i, (a, p) in enumerate(picked)]
        skills = {}
        for a, p in picked:
            for label in self.archetypes[a]["skills"]:
                if label not in skills or PRIORITY_RANK[p] < PRIORITY_RANK[skills[label]]:
                    skills[label] = p
        fi_req = dom == "care" or (dom == "cs" and rng.random() < 0.5) or (lang == "fi" and rng.random() < 0.5)
        en_req = not (fi_req and rng.random() < 0.5)
        nid, cid = f"N-{num:03d}", f"C-{num:03d}"
        need = {
            "need_id": nid, "company_id": cid, "version": 1, "confirmed": True, "lang": lang, "outcomes": outcomes,
            "skills": [{"esco_uri": self.esco[l.lower()], "label": l, "level": p} for l, p in skills.items()],
            "languages": [{"lang": "en", "required": en_req}, {"lang": "fi", "required": fi_req}],
            "constraints": {"onsite": rng.choices(["onsite", "hybrid", "remote"], weights=[35, 50, 15])[0], "start": rng.choice(STARTS),
                            "intl_ok": rng.random() < 0.8, "relocation_support": rng.choice(["none", "partial", "full"])},
            "company_reality_notes": rng.sample(self.bank[dom]["reality_notes"], rng.choice([1, 2])),
        }
        company = {"company_id": cid, "name": name, "industry": self.arch["domains"][dom]["industry"], "capacity": rng.randint(10, 15)}
        return company, need, {o["id"]: a for o, (a, _) in zip(outcomes, picked)}

    def build(self) -> dict:
        people = self.jobseekers()
        companies, needs, outcome_truth = self.needs()
        by = lambda key: sorted((p[key] for p in people), key=lambda x: x["candidate_id"])
        return {
            "companies.json": companies, "needs.json": needs,
            "jobseekers.json": by("jobseeker"), "identities.json": by("identity"), "attributes.json": by("attributes"),
            "planted.json": {"claims": {p["jobseeker"]["candidate_id"]: p["claims"] for p in people},
                             "items": {p["jobseeker"]["candidate_id"]: p["items"] for p in people},
                             "outcomes": outcome_truth, "related": sorted(map(sorted, self.arch["related"]))},
        }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--out", default=str(HERE / "out"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, obj in World(args.seed).build().items():
        (out / name).write_text(json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
