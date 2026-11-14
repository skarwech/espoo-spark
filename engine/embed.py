"""F5: multilingual dense embeddings (BAAI/bge-m3) and top-k retrieval of a candidate's units per need outcome."""
import json
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "BAAI/bge-m3"
REVISION = "9a0624b896d81da7492a910ffa53731274b6cf3d"  # the snapshot every stored retrieval was computed with; a fresh setup gets the same
TOP_K = 3


@lru_cache(maxsize=1)
def model():
    from sentence_transformers import SentenceTransformer  # slow import; only when embedding is needed
    return SentenceTransformer(MODEL_ID, revision=REVISION, cache_folder=str(ROOT / ".models"), device="cpu")


@lru_cache(maxsize=1)
def version() -> str:
    """Model id plus the Hugging Face snapshot hash actually loaded."""
    snaps = sorted((ROOT / ".models").glob("models--BAAI--bge-m3/snapshots/*"))
    return f"{MODEL_ID}@{snaps[-1].name if snaps else 'unknown'}"


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        return np.zeros((0, 1024), dtype=np.float32)
    return model().encode(texts, normalize_embeddings=True, batch_size=16, convert_to_numpy=True).astype(np.float32)


def retrieve(outcomes: list[dict], units: list[dict], k: int = TOP_K) -> dict[str, list[dict]]:
    """{outcome_key: [{unit_id, cosine}]} for the top-k units by cosine. Only outcome texts and unit texts are embedded."""
    if not units:
        return {o["key"]: [] for o in outcomes}
    sims = embed([o["text"] for o in outcomes]) @ embed([u["text"] for u in units]).T
    out = {}
    for o, row in zip(outcomes, sims):
        idx = np.argsort(-row, kind="stable")[:k]
        out[o["key"]] = [{"unit_id": units[i]["id"], "cosine": round(float(row[i]), 4)} for i in idx]
    return out


if __name__ == "__main__":
    a, b = embed(["Cut false alerts by half"]), embed(["Cut false alerts by half"])
    assert np.array_equal(a, b), "embedding is not deterministic"
    print(json.dumps({"version": version(), "dim": int(a.shape[1]), "deterministic": True}))
