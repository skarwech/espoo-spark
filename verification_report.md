# Verification report — F0

| Check | Kind | Status | Detail |
|---|---|---|---|
| Schema validity of all records | hard | PASS | 5/5 tests green |
| Story boxes ≤ 120 words | hard | PASS | 1/1 tests green |
| ESCO URIs exist in cache | hard | PASS | 1/1 tests green |
| PII scanner positive control (7 planted: email, 4 phone formats, ID, IBAN) | hard | PASS | 1/1 tests green |
| No real PII anywhere (0 hits) | hard | PASS | 1/1 tests green |
| Identity-leak detector positive control | hard | PASS | 1/1 tests green |
| No identity in matching records | hard | PASS | 1/1 tests green |
| Reproducibility: regenerate twice, byte-identical | hard | PASS | 1/1 tests green |
| Quotas match pre-registration | hard | PASS | 1/1 tests green |
| Planted cases exist and are labelled | hard | PASS | 1/1 tests green |
| Golden set complete (20 pairs, all labels) | hard | PASS | 1/1 tests green |
| Golden set frozen to its items (sha256) | hard | PASS | 1/1 tests green |
| Diversity: no language/background < 10% | target | PASS | smallest: lang ar = 14.7% |
| Label reliability: Cohen's κ ≥ 0.6 | target | PROXY | κ = 1.00 between two AI proxies sharing one rubric; says little about human reliability. Human labels pending. |
| Realism: ≥ 90% of 20 stories plausible | target | PROXY | 98% (AI proxy raters; human rating pending) |
