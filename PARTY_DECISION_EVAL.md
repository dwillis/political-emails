# Party labels from a decision model: nimble-v3 pilot

**Date:** 2026-10-25
**Script:** `scripts/eval_party_decision.py`
**Model:** `nimble-v3:9b-mxfp8`, run locally through Ollama's `/v1/systemone` endpoint with a single `choice` question

## Question

About 179,464 emails have a `party` value but no `committee_fec_id`. For those, can a decision model do as well as or better than the current `party` field? The model picks one of four labels:

- Democrat
- Republican
- Other Party
- Not a Political Committee

The current field can only hold D, R, I or G. I and G both count as "Other Party" here, and neither appeared in either sample.

## Bottom line

| Method | Accuracy, weighted to all 179K emails |
|---|---|
| Current `party` field | 65% |
| Model, one label per email | 89% |
| Model, one label per sender (majority vote) | 93% |
| Hybrid: model's "Not a Political Committee" per email, otherwise current label | 93% |
| Hybrid: model's "Not a Political Committee" by sender vote, otherwise current label | 94% |
| **Same, plus the candidate-disclaimer safeguard (see below)** | **95%** |

The best method combines the two. Use the model only to decide whether a sender is a political committee at all, and decide that by majority vote across the sender's emails. For senders the vote keeps as political, use the current `party` label.

This is also the cheapest way to run the model on everything. Labeling every email would take about 179,000 model calls, roughly 65 hours. A vote over up to 5 emails for each of the 4,857 senders needs about 24,000 calls, roughly 9 hours.

## Round 2: larger sample with sender voting

### Design

- **Sample:** 350 emails drawn with seed 42, weighted toward the two sources with the most disagreement in round 1:
  - 150 from `domain-map`
  - 100 from `legacy`
  - 100 from all other `party_source` values
- **Senders:** 202. A sender is the committee name, or the sending domain when there is no committee name. Domain alone would lump every advertiser on a rented list like trumptrainnews.com into one sender.
- **Vote:** each sender's label is the most common model label across up to 5 of its eligible emails, always including the sampled one. Ties go to the label with the highest total probability.
- **Model calls:** 1,030, taking about 38 minutes on an M3 Max with 2 workers.

```bash
uv run python scripts/eval_party_decision.py --strata domain-map=150,legacy=100,other=100 \
    --per-sender 5 --seed 42 --cache party_cache.jsonl -o party_eval.jsonl
```

### Judging

Where the current field, the per-email label and the sender vote all agreed, I spot-checked and accepted the label. That covered 102 senders. I judged each of the other 100 senders by who actually sent the email:

- For known candidates and organizations, I used my own knowledge.
- For unfamiliar senders, I read their emails.
- For campaigns I didn't know, I trusted the FEC candidate match.

17 emails from 10 senders were too close to call and are left out of the scores:

- American Principles Project
- Fair Fight Action
- Fairness Project (two committee spellings)
- Oregon League of Conservation Voters
- Republican Accountability Project
- the National Redistricting Action Fund
- the Gascón recall committee
- LFG America
- Team Hilton

Most are advocacy or ballot-measure groups that run explicitly electoral campaigns. Whether they count as political committees is a definitions question for you.

### Results

Counts are emails; the 17 ambiguous ones are excluded.

| Method | Correct | Wrong | domain-map | legacy | other |
|---|---|---|---|---|---|
| Current `party` field | 173 | 160 | 71/147 | 12/89 | 90/97 |
| Model, per email | 302 | 31 | 133/147 | 85/89 | 84/97 |
| Model, sender vote | 314 | 19 | 138/147 | 88/89 | 88/97 |
| Hybrid, per email | 312 | 21 | 136/147 | 85/89 | 91/97 |
| Hybrid, sender vote | 317 | 16 | 138/147 | 88/89 | 91/97 |

The accuracy percentages in the bottom-line table weight each column by its share of all eligible emails: `domain-map` 46%, `legacy` 9%, other 44%.

### What voting fixed

The vote corrects the model when one atypical email misleads it, for example:

- a candidate's non-fundraising message called "not political"
- a newsletter's one partisan-sounding issue called Republican

Examples the vote fixed:

- **Campaigns the per-email model called non-political:** Bishop for NC, Dominic for CT, Team Chavez-DeRemer, Winsome for Governor, Nicole for New York, James for NY 2026.
- **Senders that are not political committees, which the per-email model called Republican:** Advancing American Freedom, Tea Party Patriots Action, a stray Trump Train News email.
- **Wrong Other Party or Republican calls:** Castelli for Congress, Ras Smith, Claudia for Congress, Diehl for Massachusetts.

### What the hybrid adds on top of voting

When the vote picks a party, it is still less reliable than the current label. On its own, the vote mislabeled campaigns as Other Party: Faulconer, Lindstrom, Quacy Smith and Melanie D'Arrigo. It also labeled John Gregg (D, Indiana) as Republican. The hybrid avoids these by keeping the current D/R label unless the vote says "Not a Political Committee."

The tradeoff is that the hybrid keeps the current field's two outright party errors, which the model got right:

- **Hernandez for New York** is labeled D, but the email signs off "Republican for NYS Comptroller."
- **Mike Weinholtz for Governor** is labeled R, but he was Utah's 2016 Democratic nominee.

### Remaining errors (hybrid, sender vote: 16 emails)

- **End Citizens United Nonfederal: 5 emails, 1 sender.** Its fightforreform.org emails look like news alerts, so the vote said not political. It is a Democratic-aligned committee, though arguably borderline.
- **Small Democratic campaigns whose emails don't mention a party: 5 emails.** Friends of Tobias Read (two committee spellings), Keir for AG, Gashi for Legislature. Anthony D'Esposito (R) is the same kind of miss. The model is told to use only facts in the email, so it can't use outside knowledge of who these candidates are.
- **Kept from the current field: 5 emails.**
  - National Republican Polling (2): a rented-list poll mailer whose one sampled ad was from Goldco.
  - Republican Task Force: a list mailer forwarding a Heritage Foundation appeal.
  - Hernandez and Weinholtz, as described above.
- **Elect Common Sense: 1 email.** A pro-Trump committee with a paid-for disclaimer that the vote called not political.

### Safeguard: block "Not a Political Committee" when the disclaimer names a candidate committee

The safeguard keeps the current D/R label when the sender vote says "Not a Political Committee" but the "Paid for by" disclaimer looks like a candidate committee. To test that, the "Paid for by" prefix is stripped and the remaining committee name must match one of these patterns:

- "*X* for *office*" or "*X* 4 *office*", where *office* is Congress, AG, Governor, Legislature, Mayor, etc.
- "*X* for *state*", e.g. "Seth for Massachusetts"
- "Friends of *X*"
- "Committee to Elect *X*"
- anything containing "Campaign"

Each version was scored on the same 333 judged emails, counting only emails where the safeguard changes the label:

| Safeguard | Fixed | Broke | Correct | Weighted accuracy |
|---|---|---|---|---|
| None (hybrid, sender vote) | – | – | 317 | 94.3% |
| `party_source` is fec-candidate or platform | 5 | 5 | 317 | 94.3% |
| Any "Paid for by" disclaimer | 8 | 13 | 312 | 94.4% |
| fec-candidate/platform source + any disclaimer | 2 | 2 | 317 | 94.3% |
| **Candidate-style disclaimer** (any source) | **2** | **0** | **319** | **95.2%** |
| Candidate-style disclaimer + fec-candidate/platform source | 2 | 0 | 319 | 95.2% |
| Candidate-style disclaimer on any of the sender's voting emails | 2 | 0 | 319 | 95.2% |

- **Checking only `party_source` doesn't help.** It rescues campaigns like Tobias Read and D'Esposito, but it also overrides correct "Not a Political Committee" calls for Substack newsletters and advocacy groups, such as AMAC Action, Kathy Barnette's Substack and the Second Amendment Foundation.
- **Any "Paid for by" disclaimer is too broad.** Advocacy groups and legal defense funds use them too, e.g. Libertas Institute, Keystone Progress, Rudy Defense Trust and Freedom Caucus Foundation.
- **The candidate-style check is precise but narrow.** It fixed Keir for AG and Gashi for Legislature and broke nothing. Adding a source requirement, or checking the sender's other emails, changed nothing in this sample.
- **It can't help campaigns without a disclaimer.** None of the voting emails for Friends of Tobias Read or Anthony D'Esposito carries a disclaimer, so the check can't catch them.
- **Tested on only two fixes.** The pattern hasn't been run against the full data, where it could match an advocacy group named like a campaign, e.g. "Campaign for …".

With the candidate-style safeguard, the hybrid sender vote has 14 errors left. The rest of the list above is unchanged.

### What the current field gets wrong

160 of its 333 judged labels are wrong. Almost all are senders that are not political committees but carry a partisan label, most of them coming through `domain-map` and `legacy`. Examples:

- Hillsdale College (15 sampled emails)
- Trump Train News (13)
- America First War Room (10)
- American Action News (9)
- Turning Point USA, Judicial Watch, The Flyover, Heritage
- advertisers like Goldco and True Shot Ammo

On the Democratic side, the same problem shows up with:

- Friends of the Earth
- Keystone Progress
- LeftAction
- Voter Action Project

The `legacy` source was right on only 12 of 89 emails. It is effectively a list of conservative newsletters.

Two data problems also showed up:

- **"okgop" is a misassigned committee.** Its sampled emails come from True Shot Ammo, Secure & Prosper and National Republican Polling.
- **Two current labels have the wrong party:** Hernandez and Weinholtz, above.

## Full run: every sender, nimble-v3 (2026-10-26)

`scripts/classify_senders.py` applied the recommended method to all eligible senders: a sender vote plus the candidate-disclaimer safeguard.

- **Coverage:** 4,854 senders and 179,464 emails, using up to 5 voting emails per sender.
- **Run:** 13,067 model calls with no errors, taking 6h15m on an M3 Max.
- **Output:** written to `state/validation/sender_party/`, which git ignores. The existing `party` field and `data/` are unchanged.
  - `senders.csv` has one row per sender: votes, vote result, safeguard result and the flag.
  - `model_results.jsonl` holds every model answer and is reused if the run is repeated.
  - `review_queue.csv` lists flagged senders to check by hand.

**Result: 1,687 senders (74,540 emails, 42%) flagged as not political committees.**

| | Flagged emails |
|---|---|
| Current party R | 62,900 of 99,661 (63%) |
| Current party D | 11,540 of 79,618 (14%) |
| Source `legacy` | 9,962 of 10,843 (92%) |
| Source `domain-map` | 53,366 of 91,005 (59%) |
| Source `committee-majority` | 1,197 of 1,715 (70%) |
| Source `platform` | 4,096 of 15,535 (26%) |
| Source `override` | 750 of 3,046 (25%) |
| Source `fec-candidate` | 5,160 of 43,700 (12%) |
| Source `committee-name` | 9 of 13,620 (0%) |

- **The flagged list is concentrated.** The 50 largest flagged senders account for 72% of flagged emails, and the top 200 for 92%. Examples: American Action News, Trump Train News, Hillsdale College, American Briefing, Turning Point USA.
- **The safeguard kept 49 senders (1,581 emails) as political** despite a "not political" vote. Most are real campaigns, e.g. Simon for Secretary of State, Friends of Nan Whaley, Garrity for PA and Keir for AG. A few are not: the International Mission Board and a rightusa.org list.
- **The model exposed bad committee names.** Many flagged senders whose committee is named "X for Congress" are personal newsletters, stores or Substacks wrongly attached to a campaign committee, so the model's flag is right. Examples:
  - Don Jr., Kirk Cameron, Larry Kudlow, Dinesh D'Souza, Sean Spicer, Matt Mackowiak ("for Congress")
  - "Trump for President" emails from the Official Trump Store and Secure & Prosper
- **Some real campaigns were still flagged.** These need review, along with End Citizens United Nonfederal (3,390 emails, a known error from the evaluation). Examples:
  - Friends of Tobias Read, Gina Raimondo for Governor
  - Friends of Rushern Baker III, Jeff Jackson for Attorney General
  - Anthony D'Esposito, Theresa Coates Ellis
- **Some senders are too small to vote on.** 787 flagged senders have only one email, so their flag comes from that one email.

`review_queue.csv` holds 242 senders covering 69,336 of the 74,540 flagged emails (93%): the 200 largest flagged senders plus 42 smaller ones with campaign-like names and at least 10 emails. It has blank `reviewer_decision` and `notes` columns to fill in.

## Model comparison: nimble-v3, clef-flash, d3

All three decision models ran on the same 350 emails and the same voting emails (1,030 calls each), with the same question, through the same script. None returned errors, and each run took 27–29 minutes on an M3 Max.

| Model | License | Per email | Sender vote | Hybrid, sender vote | Hybrid, vote + safeguard |
|---|---|---|---|---|---|
| `nimble-v3:9b-mxfp8` (Bespoke Labs) | CC BY-NC 4.0 (noncommercial) | 89.2% | 92.9% | 94.3% | **95.2%** |
| `clef-flash` (Cloudflare) | Apache 2.0 | 89.1% | 89.6% | 94.7% | 94.7% |
| `d3:9b` (vLLM Semantic Router) | Apache 2.0 | 91.0% | 90.6% | 92.6% | 92.6% |

Accuracy is weighted to all eligible emails. For reference, the current `party` field scores 64.8%.

The hybrid only uses each model to answer one question: is this sender a political committee? So the key comparison is how well each model's sender vote does on that question:

| Model | Flags political committees as not political (false positives) | Misses non-political senders (false negatives) | Precision | Recall |
|---|---|---|---|---|
| nimble-v3 | 11 | 3 | 93.4% | 98.1% |
| clef-flash | 7 | 9 | 95.5% | 94.3% |
| d3:9b | 0 | 32 | 100% | 79.7% |

Each model fails in a different way:

- **nimble-v3** finds almost every non-political sender but sometimes says it about real campaigns. The candidate-disclaimer safeguard recovers some of those campaigns.
- **clef-flash** is more balanced. Its misses are conservative advocacy groups and lists it treats as political: Charlie Kirk, RedState, Tea Party Patriots Action, Action 4 Liberty. Its errors on campaigns are End Citizens United Nonfederal, Dominic for CT and Tobias Read.
- **d3:9b** never calls a political committee non-political, but it treats a fifth of the non-political senders as political. Most are nonprofit foundations and advocacy groups like Turning Point USA, American Cornerstone Institute, AMAC Action and America First Legal. When d3 does pick D or R, it is the most accurate of the three (166 of 175 vs. 149 of 161 for nimble-v3). The hybrid doesn't use that, because it keeps the current D/R label.

**Combining models doesn't help.**

| Rule for flagging "not a political committee" | Weighted accuracy |
|---|---|
| nimble-v3 alone, with safeguard | 95.2% |
| At least 2 of the 3 models agree | 95.2% |
| Any of the 3 models | 94.4% |
| nimble-v3 and clef-flash both agree | 94.6% |

The 2-of-3 vote ties nimble-v3 alone and costs three times as many model calls.

**Choosing a model:** nimble-v3 and clef-flash are effectively tied. The gap is 4 emails out of 333, well inside the roughly ±3-point uncertainty of a sample this size. Use clef-flash if licensing matters, since it is Apache 2.0, and nimble-v3 otherwise. d3:9b is the weakest for this task. One untested tweak for clef-flash: its documentation says ties go to the first-listed option, and this question lists Democrat first.

The 102 senders I accepted as correct without checking could bias the scores toward nimble-v3. In practice, only 3 unchecked senders drew new disagreements from d3 or clef-flash (Diehl for MA, the Vivek Ramaswamy committee and Brandon Presley), and in all 3 the new model was wrong. So that bias doesn't change the ranking.

```bash
uv run python scripts/eval_party_decision.py --strata domain-map=150,legacy=100,other=100 \
    --per-sender 5 --seed 42 --model clef-flash:latest --cache party_cache.jsonl -o party_eval_clef.jsonl
```

## Round 1: 100-email simple random sample

The first run sampled 100 emails at random (seed 42), with no weighting and no voting.

| Method | Correct | Wrong | Ambiguous |
|---|---|---|---|
| Current `party` field | 64 | 33 | 3 |
| Model, per email | 86 | 11 | 3 |
| Hybrid, per email | 92 | 5 | 3 |

The pattern matches round 2:

- The current field's errors are mostly senders that aren't political committees.
- The model's errors are mostly real campaigns whose emails never state a party, e.g. a Wes Moore 9/11 message, a Tate Reeves event invite, and "Green for Governor" read as the Green Party.

## Caveats

- **Single-judge truth.** Every label was judged once, by me. A second reviewer would likely disagree on some advocacy groups, and the 17 ambiguous emails could swing several points depending on how they are defined.
- **Agreement accepted as correct.** Where all three methods agreed, I accepted the label after a spot check rather than verifying each one.
- **Committee names are messy.** The same committee can appear under several spellings (Tobias Read, Fairness Project), and the committee field is sometimes wrong (okgop). Votes split across spellings, and a wrong committee name groups unrelated emails together.
- **Most senders are small.** Half of the 4,857 senders have 2 or fewer eligible emails, and 2,189 have only 1, so voting can't help them.
- **Sample size.** With 333 judged emails, the accuracy estimates are good to roughly ±3 points. The weighted figures depend on the smaller per-group samples.

## Possible next steps

1. **Run the hybrid with sender votes and the candidate-disclaimer safeguard over all 4,857 senders.** Use clef-flash if the noncommercial license is a problem, nimble-v3 otherwise. That's about 24,000 model calls, roughly 9 hours. Write the result as a new field, e.g. `is_political_committee` plus its source, instead of overwriting `party`.
2. **Merge committee-name variants before voting,** e.g. "friends of tobias read" and "friends of tobias read (5208)."
3. **Decide where advocacy, ballot-measure and legal-defense groups belong.** Then encode that in the "Not a Political Committee" definition and re-judge the 10 ambiguous senders.
4. **Fix the two wrong party labels and the okgop committee assignment** using the existing override mechanism.
