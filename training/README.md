# Training the impact model

The impact model rates a request LOW / MID / HIGH by how much damage a mistaken action could do. It feeds the
risk score (see `DESIGN_DECISIONS.md`).

```
python3 generate_data.py      # ~3,500 broad examples from an LLM (needs the key in ../.env)
python3 generate_focus.py     # targeted examples for the weak spots (amounts, mass messaging, overwrites, documents, security)
python3 train_impact.py       # trains, compares and saves ../models/model_impact_v2.joblib + metrics
python3 ../tests/test_impact_model.py
```

**Model:** TF-IDF on words and on characters (so misspellings still match), plus `../impact_text.py`, which adds plain words
for the size of any amount ("₹5000" vs "₹80 lakh"), the size of any count ("5 customers" vs "50,000 users") and "all/every".
Classifier: logistic regression, regularisation chosen by 5-fold cross-validation, class-balanced, with typo-augmented training copies.

**Data:** 6,320 LLM-written, labelled prompts across 12 business domains and several writing styles. The labels follow the
class definitions in `generate_data.py`, so the model learns *this project's* idea of impact, not an external ground truth.
Prompts that appear in a test set are removed from training.

**Measured (hand-written prompts, one author, small sets):**

| | Original model | Rules only | New model |
|---|---|---|---|
| Test set 1 (83 prompts, used to choose improvements) | 43.4% | 49.4% | **92.8%** |
| Test set 2 (60 prompts, not used for tuning) | 46.7% | 58.3% | **96.7%** |

When the model is at least 60% sure, it was right 96% of the time on both sets. Set 2 is the cleaner number, but it was written
by the same person as set 1 and its prompts overlap in theme with the data-generation topics, so treat both as optimistic until
checked on prompts from someone else.

**In the pipeline:** the model leads the impact estimate when it is at least 60% sure (weight 0.5, 0.65 or 0.8 by confidence).
It can raise a score freely but lower it by at most 2.5. Below 60% it is ignored; it is not consulted for read-only requests or
document edits. On the 143 test prompts this made the impact stage right 92% of the time (69% with the earlier 50/50 blend,
53% with rules alone) and cut dangerous under-rating from 33 prompts to 6.

**Known weaknesses:** it can read an ordinary lookup ("Look up the status of order…") as MEDIUM; a mass email described as
"already sent" can look MEDIUM (the rules still block it); and it only knows the kinds of request it was trained on.
Retrain with `generate_*.py` plus prompts from real use to improve it.
