# TA Web App (Desktop)

## What it does
- Upload one or more chart screenshots (e.g., 1H/1D/1W)
- Generates structured technical analysis using either GitHub Models or OpenAI
- Lets you download the analysis as a `.docx` report

## Run
1. Install dependencies:
   - `pip install -r requirements.txt`
2. Start app:
   - `streamlit run ta_web_app.py`
3. Open the local URL shown in terminal (usually `http://localhost:8501`)

## Notes
- Provider options in app:
   - GitHub Models (recommended if you want GitHub ecosystem)
   - OpenAI
- GitHub Models setup:
   - Set `GITHUB_TOKEN`
   - Optional: set `GITHUB_MODEL` (default: `openai/gpt-4.1`)
   - Optional: set `GITHUB_MODELS_BASE_URL` (default in app)
- OpenAI setup:
   - Set `OPENAI_API_KEY`
   - Optional: set `OPENAI_MODEL`
