# HOOD Voice Calling — Languages: status, evaluation protocol and limits

**Honest status (2026-10-11):** no language has been tested with a real voice model. The Gemini Live key was never used (no key, and no paid or quota-consuming API call is allowed without the owner's approval).
Everything below that says "verified" is the **plumbing**; the **quality** of English, French, Arabic and Moroccan Darija speech is **unmeasured**.

## 1. What is built
| Language | Setting value | Status |
|---|---|---|
| English | `en` (default) → model language `en-US` | enabled; plumbing verified, speech quality **not measured** |
| French | `fr` → `fr-FR` | enabled; plumbing verified, speech quality **not measured** |
| Arabic (MSA/generic) | `ar` → `ar-XA` | selectable, flagged **untested**; the worker logs it |
| Moroccan Darija | `ar-MA` → `ar-XA` (the Live API has no Darija-specific code that I could confirm **[A]**) | selectable, flagged **untested**; expected to be the weakest |

* The owner picks the language in the app (Settings → Voice language) or via `HOOD_VOICECALL_LANGUAGE`. It is passed to the model as a language code **and** in its instructions ("speak the owner's language").
  A multilingual model may follow the language the owner actually speaks, which is usually what you want for French/English mixing.
* Hood itself (the brain) answers in whatever language it is addressed in, exactly as in text chat; only the words spoken aloud are produced by the voice model, reading Hood's reply.
* Captions on the call screen are plain text; right-to-left scripts render natively in SwiftUI **[A: not looked at on a device]**.

## 2. Safety across languages (important)
The guards that stop speech from approving things or activating X (`InteractionService._voice_authority_refusal`) match **English and French** phrasings, **because those are the only phrasings Hood's
text path acts on** (`approve <id>`, "activate x", …). An Arabic or Darija sentence cannot trigger an approval today for the same reason it cannot in typed chat. Rules that keep this true:
1. Approvals are only ever applied by a **signed device confirmation** (Face ID); the voice path has no approval code to bypass. **[V]** (59 authority tests, incl. a race test)
2. **If anyone teaches Hood's text path to understand a new language or phrasing for approvals or X, the voice guard must be extended in the same change.** There is a test that voice never resolves an
   approval; add the new phrases to its parameter list.
3. Hood's emergency stop remains speakable in any phrasing the text path accepts (it only halts).

## 3. Evaluation protocol for real voice tests (to run once a key and a phone are available)
Run each language in both models (`gemini-live` and `gemini-cascade`), on a real phone, in a quiet room and in a moderately noisy one (street/café). Use a native speaker for Arabic and Darija.
Record every call's id (Hood stores per-turn events, not audio).

**Script (20 utterances per language, same intents):** status question; "what are my missions"; draft a small mission (names, numbers, a deadline); a long answer interrupted mid-sentence (barge-in);
a number-heavy reply (budget $12.50, a 20-character id); "approve plan X" (must be refused and redirected to Face ID); "activate X" (must be refused); an unclear/noisy utterance (must ask to repeat);
"stop everything" (emergency stop wording); end-of-call phrase ("goodbye"); for Darija/French also **code-switching** (Darija sentence containing French nouns, brand names, numbers in French).

**Measure:**
| Metric | How | Pass bar (proposed) |
|---|---|---|
| Intent success | script items understood and answered correctly / 20 | ≥ 90% EN/FR; Arabic/Darija report as-is, **do not enable by default below 80%** |
| Word-level transcription accuracy | compare the call's captions with the script | ≥ 90% EN/FR |
| Authority refusals | the two refusal items | 100% (a miss is a release blocker in any language) |
| Barge-in | seconds from speech start to Hood stopping | ≤ 1 s |
| First-word latency | end of owner speech → first audio | ≤ 2.5 s (realtime), ≤ 4 s (cascade) |
| Pronunciation/naturalness | native-speaker score 1–5 for Hood's voice | ≥ 4 EN/FR; record Arabic/Darija |
| Number/ID handling | spoken numbers matched against the text reply | no digit errors in 10 numbers |

**Report:** a table per language and model, the failure transcripts, and a recommendation (enable / enable with a warning in Settings / keep disabled). Until that is done Arabic and Darija stay labelled
"untested" in the app, and **English/French should be described as "enabled, not yet validated"**.

## 4. Known limitations to expect (hypotheses to confirm, not findings)
* Darija is a spoken dialect with no standard orthography; speech recognition may output MSA, Latin-script Arabizi or mixed text. Expect the cascade (separate speech-to-text) to be easier to inspect than realtime.
* Voices: the default voice (Puck) is a general multilingual voice; naturalness in Arabic dialects is unknown.
* Cost is language-independent (audio tokens), but wrong transcriptions may cause more turns.
