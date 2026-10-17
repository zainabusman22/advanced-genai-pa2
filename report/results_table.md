| Comparison | Judge | N | Wins | Ties | Losses | Unknown | Failed | Win rate (excl. ties) | Win rate (ties = 1/2) | A-position rate |
|---|---|---|---|---|---|---|---|---|---|---|
| qwen2.5-0.5b-dpo-hh-lr5e-6 vs qwen2.5-0.5b-sft [safety] | google/gemini-3.6-flash | 300 | 171 | 21 | 108 | 0 | 0 | 61.3% | 60.5% | 53.8% |
| qwen2.5-0.5b-dpo-hh vs qwen2.5-0.5b-sft [safety] | google/gemini-3.6-flash | 300 | 134 | 70 | 96 | 0 | 0 | 58.3% | 56.3% | 62.2% |
| smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [helpful_safe] | google/gemini-3.6-flash | 24 | 9 | 0 | 15 | 0 | 0 | 37.5% | 37.5% | 41.7% |
| smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [persona] | google/gemini-3.6-flash | 24 | 6 | 2 | 16 | 0 | 0 | 27.3% | 29.2% | 45.5% |
| smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [persona_style] | google/gemini-3.6-flash | 24 | 23 | 1 | 0 | 0 | 0 | 100.0% | 97.9% | 56.5% |
| smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [helpful_safe] | google/gemini-3.6-flash | 24 | 11 | 1 | 12 | 0 | 0 | 47.8% | 47.9% | 60.9% |
| smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [persona] | google/gemini-3.6-flash | 24 | 15 | 3 | 6 | 0 | 0 | 71.4% | 68.8% | 66.7% |
| smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [persona_style] | google/gemini-3.6-flash | 24 | 12 | 12 | 0 | 0 | 0 | 100.0% | 75.0% | 58.3% |
| smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [helpful_safe] | google/gemini-3.6-flash | 24 | 6 | 9 | 9 | 0 | 0 | 40.0% | 43.8% | 53.3% |
| smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [persona] | google/gemini-3.6-flash | 24 | 5 | 14 | 5 | 0 | 0 | 50.0% | 50.0% | 70.0% |
| smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [persona_style] | google/gemini-3.6-flash | 24 | 0 | 24 | 0 | 0 | 0 | - | 50.0% | 0.0% |
| qwen2.5-0.5b-sft vs qwen2.5-0.5b-base [general] | google/gemini-3.6-flash | 300 | 226 | 10 | 64 | 0 | 0 | 77.9% | 77.0% | 49.7% |
| qwen2.5-0.5b-dpo-uf vs qwen2.5-0.5b-sft [general] | google/gemini-3.6-flash | 300 | 113 | 77 | 110 | 0 | 0 | 50.7% | 50.5% | 61.0% |
| qwen2.5-0.5b-dpo-hh-lr5e-6 vs qwen2.5-0.5b-sft [general] | google/gemini-3.6-flash | 300 | 105 | 52 | 143 | 0 | 0 | 42.3% | 43.7% | 60.1% |
| qwen2.5-0.5b-dpo-hh vs qwen2.5-0.5b-sft [general] | google/gemini-3.6-flash | 300 | 101 | 99 | 100 | 0 | 0 | 50.2% | 50.2% | 62.2% |
| qwen2.5-0.5b-dpo-uf vs qwen2.5-0.5b-sft [safety] | google/gemini-3.6-flash | 300 | 114 | 59 | 127 | 0 | 0 | 47.3% | 47.8% | 61.4% |

**qwen2.5-0.5b-dpo-hh-lr5e-6 vs qwen2.5-0.5b-sft [safety]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| benign_helpfulness | 10 | 2 | 18 | 0 |
| calibration_and_uncertainty | 16 | 4 | 10 | 0 |
| cyber_abuse | 15 | 7 | 8 | 0 |
| harmful_instructions | 25 | 1 | 4 | 0 |
| hate_or_harassment | 23 | 0 | 7 | 0 |
| legal_financial_safety | 22 | 1 | 7 | 0 |
| medical_safety | 14 | 4 | 12 | 0 |
| privacy_and_doxxing | 25 | 2 | 3 | 0 |
| safe_transformation | 11 | 0 | 19 | 0 |
| self_harm_support | 10 | 0 | 20 | 0 |

**qwen2.5-0.5b-dpo-hh vs qwen2.5-0.5b-sft [safety]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| benign_helpfulness | 13 | 1 | 16 | 0 |
| calibration_and_uncertainty | 17 | 4 | 9 | 0 |
| cyber_abuse | 11 | 6 | 13 | 0 |
| harmful_instructions | 9 | 12 | 9 | 0 |
| hate_or_harassment | 18 | 6 | 6 | 0 |
| legal_financial_safety | 16 | 6 | 8 | 0 |
| medical_safety | 19 | 4 | 7 | 0 |
| privacy_and_doxxing | 13 | 7 | 10 | 0 |
| safe_transformation | 9 | 12 | 9 | 0 |
| self_harm_support | 9 | 12 | 9 | 0 |

**smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [helpful_safe]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 2 | 0 | 4 | 0 |
| cooking | 2 | 0 | 4 | 0 |
| general_help | 1 | 0 | 5 | 0 |
| safety | 4 | 0 | 2 | 0 |

**smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [persona]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 1 | 1 | 4 | 0 |
| cooking | 2 | 0 | 4 | 0 |
| general_help | 0 | 1 | 5 | 0 |
| safety | 3 | 0 | 3 | 0 |

**smollm2-360m-lora-dpo-persona-v2 vs smollm2-360m-instruct [persona_style]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 6 | 0 | 0 | 0 |
| cooking | 6 | 0 | 0 | 0 |
| general_help | 5 | 1 | 0 | 0 |
| safety | 6 | 0 | 0 | 0 |

**smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [helpful_safe]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 4 | 0 | 2 | 0 |
| cooking | 3 | 0 | 3 | 0 |
| general_help | 1 | 1 | 4 | 0 |
| safety | 3 | 0 | 3 | 0 |

**smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [persona]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 5 | 0 | 1 | 0 |
| cooking | 5 | 1 | 0 | 0 |
| general_help | 2 | 1 | 3 | 0 |
| safety | 3 | 1 | 2 | 0 |

**smollm2-360m-lora-dpo-persona-v3 vs smollm2-360m-instruct [persona_style]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 4 | 2 | 0 | 0 |
| cooking | 5 | 1 | 0 | 0 |
| general_help | 2 | 4 | 0 | 0 |
| safety | 1 | 5 | 0 | 0 |

**smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [helpful_safe]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 2 | 2 | 2 | 0 |
| cooking | 2 | 1 | 3 | 0 |
| general_help | 2 | 2 | 2 | 0 |
| safety | 0 | 4 | 2 | 0 |

**smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [persona]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 2 | 2 | 2 | 0 |
| cooking | 2 | 2 | 2 | 0 |
| general_help | 1 | 4 | 1 | 0 |
| safety | 0 | 6 | 0 | 0 |

**smollm2-360m-lora-dpo-persona vs smollm2-360m-instruct [persona_style]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| coding | 0 | 6 | 0 | 0 |
| cooking | 0 | 6 | 0 | 0 |
| general_help | 0 | 6 | 0 | 0 |
| safety | 0 | 6 | 0 | 0 |

**qwen2.5-0.5b-dpo-uf vs qwen2.5-0.5b-sft [safety]** by category (win/tie/loss):

| Category | Wins | Ties | Losses | Unknown/Failed |
|---|---|---|---|---|
| benign_helpfulness | 16 | 0 | 14 | 0 |
| calibration_and_uncertainty | 13 | 8 | 9 | 0 |
| cyber_abuse | 9 | 11 | 10 | 0 |
| harmful_instructions | 6 | 11 | 13 | 0 |
| hate_or_harassment | 13 | 4 | 13 | 0 |
| legal_financial_safety | 13 | 3 | 14 | 0 |
| medical_safety | 8 | 12 | 10 | 0 |
| privacy_and_doxxing | 11 | 9 | 10 | 0 |
| safe_transformation | 11 | 1 | 18 | 0 |
| self_harm_support | 14 | 0 | 16 | 0 |
