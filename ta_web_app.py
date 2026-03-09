import base64
import hashlib
import io
import os
from pathlib import Path
from datetime import datetime

import streamlit as st
from docx import Document
from docx.shared import Pt
from PIL import Image
import requests
import truststore
from openai import OpenAI
from openai import APIConnectionError, APIStatusError, AuthenticationError, BadRequestError, RateLimitError


st.set_page_config(page_title="Commodities TA Analyzer", layout="wide")

truststore.inject_into_ssl()

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def to_data_url(uploaded_file, max_dimension: int = 1800, jpeg_quality: int = 85) -> str:
    uploaded_file.seek(0)
    image = Image.open(uploaded_file)
    image = image.convert("RGB")

    width, height = image.size
    longest_side = max(width, height)
    if longest_side > max_dimension:
        scale = max_dimension / longest_side
        resized = (int(width * scale), int(height * scale))
        image = image.resize(resized, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=jpeg_quality, optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return f"data:image/jpeg;base64,{encoded}"


def build_prompt(instrument: str) -> str:
    instrument_line = instrument.strip() or "Instrument not specified"
    return f"""
You are a senior technical analyst specializing in energy and commodity markets.
Analyze the uploaded chart screenshots and produce a professional trading-desk technical briefing.

Instrument: {instrument_line}
Date context: {datetime.now().strftime('%Y-%m-%d')}

Required structure:
1) Instrument + Contract
2) Intraday bias / Short-term bias (Daily) / Medium-term bias (Weekly)
3) Key Levels: Support (3) and Resistance (3)
4) Primary Trade Idea (trend-aligned): direction, entry, target, stretch target, stop, reason, invalidation
5) Intraday (Hourly) Structure: 2-4 concise observations + takeaway
6) Daily Structure: 2-4 concise observations + takeaway
7) Weekly Structure: 2-4 concise observations + takeaway
8) Momentum & Indicators: RSI, Stochastic, MACD for Daily and Weekly + takeaway
9) Bottom Line
10) Alternative Trade Setup (lower probability)

Rules:
- Prioritize price action and structure over indicators.
- Use concrete levels from charts where visible.
- Write concise, professional, actionable language.
- If some indicator is not visible, state that explicitly.
""".strip()


def generate_analysis(client: OpenAI, model: str, prompt: str, image_data_urls: list[str]) -> str:
    content = [{"type": "text", "text": prompt}]
    for url in image_data_urls:
        content.append({"type": "image_url", "image_url": {"url": url}})

    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=0.2,
        max_tokens=2000,
        timeout=120,
    )
    if not response.choices:
        return "No analysis generated."
    message_content = response.choices[0].message.content
    if isinstance(message_content, str):
        return message_content
    if isinstance(message_content, list):
        text_parts = [
            item.get("text", "")
            for item in message_content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(part for part in text_parts if part).strip() or "No analysis generated."
    return "No analysis generated."


def build_client(api_key: str, base_url: str = "") -> OpenAI:
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def normalize_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def github_base_url_candidates(base_url: str) -> list[str]:
    candidates: list[str] = []
    primary = normalize_base_url(base_url)
    if primary:
        candidates.append(primary)

    known = [
        "https://models.github.ai/inference",
        "https://models.inference.ai.azure.com",
        "https://models.github.ai",
    ]
    for item in known:
        if item not in candidates:
            candidates.append(item)
    return candidates


def github_model_candidates(model: str) -> list[str]:
    candidates: list[str] = []
    primary = (model or "").strip()
    if primary:
        candidates.append(primary)

    known = [
        "auto",
        "openai/gpt-4o",
        "gpt-4o",
        "openai/gpt-4.1",
        "gpt-4.1",
        "openai/gpt-4o-mini",
    ]
    for item in known:
        if item not in candidates:
            candidates.append(item)
    return candidates


def is_unknown_model_error(error: APIStatusError) -> bool:
    message = str(error).lower()
    return error.status_code == 400 and ("unknown_model" in message or "unknown model" in message)


def test_provider_connection(client: OpenAI, model: str) -> None:
    client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "ping"}],
        max_tokens=16,
        timeout=30,
    )


def test_https_reachability(url: str) -> tuple[bool, str]:
    try:
        response = requests.get(url, timeout=15)
        return True, f"Reachable. HTTP {response.status_code}"
    except Exception as error:
        return False, str(error)


def get_working_client(provider: str, api_key: str, base_url: str, model: str) -> tuple[OpenAI, str, str]:
    if provider != "GitHub Models (Copilot-style)":
        return build_client(api_key, ""), "", model

    last_error = None
    for candidate in github_base_url_candidates(base_url):
        client = build_client(api_key, candidate)
        for candidate_model in github_model_candidates(model):
            try:
                test_provider_connection(client, candidate_model)
                return client, candidate, candidate_model
            except APIStatusError as error:
                last_error = error
                if error.status_code == 404:
                    break
                if is_unknown_model_error(error):
                    continue
                raise
            except Exception as error:
                last_error = error
                continue

    if last_error:
        raise last_error
    raise RuntimeError("Unable to find a working GitHub Models endpoint/model combination.")


def persist_uploads(uploaded_files) -> tuple[list[str], list[Path], str]:
    data_urls: list[str] = []
    saved_paths: list[Path] = []
    hasher = hashlib.sha256()

    batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index, uploaded in enumerate(uploaded_files, start=1):
        uploaded.seek(0)
        raw_bytes = uploaded.read()
        uploaded.seek(0)

        hasher.update(raw_bytes)
        hasher.update(uploaded.name.encode("utf-8"))

        suffix = Path(uploaded.name).suffix or ".png"
        safe_name = f"{batch_id}_{index}{suffix}"
        save_path = UPLOAD_DIR / safe_name
        save_path.write_bytes(raw_bytes)
        saved_paths.append(save_path)

        data_urls.append(to_data_url(uploaded))

    return data_urls, saved_paths, hasher.hexdigest()


def analysis_to_docx_bytes(title: str, analysis: str) -> bytes:
    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(title, level=1)
    for line in analysis.splitlines():
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph("")
        elif stripped.startswith("-") or stripped.startswith("•"):
            doc.add_paragraph(stripped.lstrip("-• "), style="List Bullet")
        else:
            doc.add_paragraph(stripped)

    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer.read()


st.title("Commodities TA Analyzer")
st.caption("Upload chart screenshots (1H/1D/1W) and generate a structured technical analysis report.")

st.info("Upload screenshots and the app auto-runs analysis. Screenshots are saved locally in the uploads folder.")

provider = st.selectbox("Provider", ["GitHub Models (Copilot-style)", "OpenAI"], index=0)

if provider == "GitHub Models (Copilot-style)":
    api_key = st.text_input(
        "GitHub Token",
        type="password",
        value=os.getenv("GITHUB_TOKEN", ""),
        help="Use a GitHub token with access to GitHub Models.",
    )
    base_url = st.text_input(
        "GitHub Models Base URL",
        value=os.getenv("GITHUB_MODELS_BASE_URL", "https://models.github.ai/inference"),
        help="Use https://models.inference.ai.azure.com if your network blocks models.github.ai.",
    )
    default_model = os.getenv("GITHUB_MODEL", "auto")
    https_test_url = "https://models.github.ai/inference"
    connection_label = "Test GitHub Models Connection"
else:
    api_key = st.text_input(
        "OpenAI API Key",
        type="password",
        value=os.getenv("OPENAI_API_KEY", ""),
        help="Required for analysis generation. Used only during this session.",
    )
    base_url = ""
    default_model = os.getenv("OPENAI_MODEL", "gpt-4.1")
    https_test_url = "https://api.openai.com/v1/models"
    connection_label = "Test OpenAI Connection"

with st.expander("Advanced (optional)"):
    model_override = st.text_input(
        "Model override",
        value="",
        help="Leave empty to use automatic/default model selection.",
    )

model = model_override.strip() or default_model

if st.button(connection_label):
    if not api_key:
        st.error("Please provide an API key/token first.")
    else:
        try:
            with st.spinner("Testing connection..."):
                if provider == "GitHub Models (Copilot-style)":
                    client, resolved_base_url, resolved_model = get_working_client(provider, api_key, base_url, model)
                    if normalize_base_url(base_url) != normalize_base_url(resolved_base_url):
                        st.warning(f"Input endpoint returned 404. Using fallback endpoint: {resolved_base_url}")
                    if model_override.strip() and model.strip() != resolved_model:
                        st.warning(f"Model '{model}' unavailable. Using fallback model: {resolved_model}")
                else:
                    client = build_client(api_key, "")
                    test_provider_connection(client, model)
            st.success("Connection test passed.")
        except AuthenticationError as error:
            st.error(f"Authentication failed: {error}")
        except APIConnectionError as error:
            st.error(f"Connection error: {error}")
            st.info("Verify outbound HTTPS access and corporate proxy settings for the selected provider endpoint.")
        except APIStatusError as error:
            st.error(f"API status error ({error.status_code}): {error}")
        except Exception as error:
            st.error(f"Connection test failed: {error}")

if st.button("Test HTTPS (SSL/Proxy)"):
    ok, message = test_https_reachability(https_test_url)
    if ok:
        st.success(f"HTTPS check passed: {message}")
    else:
        st.error(f"HTTPS check failed: {message}")
        st.info("If you are on a corporate network, verify proxy settings and outbound SSL inspection rules for this endpoint.")

instrument = st.text_input("Instrument + Contract", value="Dutch TTF Natural Gas Futures (ICE Endex)")

uploads = st.file_uploader(
    "Upload screenshots (PNG/JPG)",
    type=["png", "jpg", "jpeg"],
    accept_multiple_files=True,
)

if uploads:
    cols = st.columns(min(3, len(uploads)))
    for idx, up in enumerate(uploads):
        cols[idx % len(cols)].image(up, caption=up.name, width="stretch")

if "last_upload_signature" not in st.session_state:
    st.session_state.last_upload_signature = ""
if "latest_analysis" not in st.session_state:
    st.session_state.latest_analysis = ""
if "latest_doc_name" not in st.session_state:
    st.session_state.latest_doc_name = ""
if "latest_doc_bytes" not in st.session_state:
    st.session_state.latest_doc_bytes = b""
if "latest_saved_paths" not in st.session_state:
    st.session_state.latest_saved_paths = []
if "active_provider" not in st.session_state:
    st.session_state.active_provider = ""
if "active_endpoint" not in st.session_state:
    st.session_state.active_endpoint = ""
if "active_model" not in st.session_state:
    st.session_state.active_model = ""

if uploads and api_key:
    try:
        data_urls, saved_paths, upload_signature = persist_uploads(uploads)
        st.session_state.latest_saved_paths = [str(path) for path in saved_paths]

        if upload_signature != st.session_state.last_upload_signature:
            with st.spinner("New screenshots detected. Running analysis automatically..."):
                if provider == "GitHub Models (Copilot-style)":
                    client, resolved_base_url, resolved_model = get_working_client(provider, api_key, base_url, model)
                    if normalize_base_url(base_url) != normalize_base_url(resolved_base_url):
                        st.warning(f"Input endpoint returned 404. Using fallback endpoint: {resolved_base_url}")
                else:
                    client = build_client(api_key, "")
                    resolved_model = model
                    resolved_base_url = "https://api.openai.com"
                prompt = build_prompt(instrument)
                analysis = generate_analysis(client, resolved_model, prompt, data_urls)
                doc_name = f"TA_Analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.docx"
                doc_bytes = analysis_to_docx_bytes(instrument, analysis)

            st.session_state.last_upload_signature = upload_signature
            st.session_state.latest_analysis = analysis
            st.session_state.latest_doc_name = doc_name
            st.session_state.latest_doc_bytes = doc_bytes
            st.session_state.active_provider = provider
            st.session_state.active_endpoint = resolved_base_url
            st.session_state.active_model = resolved_model
    except AuthenticationError as error:
        st.error(f"Authentication failed: {error}")
    except RateLimitError as error:
        st.error(f"Rate limit reached: {error}")
        st.info("Retry in a moment, reduce request frequency, or switch to a different model.")
    except APIConnectionError as error:
        st.error(f"Connection error: {error}")
        st.info("Verify outbound HTTPS access and corporate proxy settings for the selected provider endpoint.")
    except BadRequestError as error:
        st.error(f"Request rejected: {error}")
        st.info("Try fewer screenshots or lower-resolution images. The app auto-compresses images, but very large batches can still fail.")
    except APIStatusError as error:
        st.error(f"API status error ({error.status_code}): {error}")
        if error.status_code == 404 and provider == "GitHub Models (Copilot-style)":
            st.info("404 usually means endpoint mismatch. Try base URL https://models.inference.ai.azure.com or run the connection test to auto-fallback.")
    except Exception as error:
        st.error(f"Failed to generate analysis: {error}")

if uploads and not api_key:
    if provider == "GitHub Models (Copilot-style)":
        st.warning("Please provide a GitHub token to run automatic analysis.")
    else:
        st.warning("Please provide an OpenAI API key to run automatic analysis.")

if st.session_state.latest_saved_paths:
    st.subheader("Saved Screenshots")
    for path in st.session_state.latest_saved_paths:
        st.write(path)

if st.session_state.latest_analysis:
    st.subheader("Active Runtime")
    st.write(f"Provider: {st.session_state.active_provider}")
    st.write(f"Endpoint: {st.session_state.active_endpoint}")
    st.write(f"Model: {st.session_state.active_model}")

    st.subheader("Analysis")
    st.write(st.session_state.latest_analysis)
    st.download_button(
        "Download Word Report",
        data=st.session_state.latest_doc_bytes,
        file_name=st.session_state.latest_doc_name,
        mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

st.divider()
st.markdown("""
**How to use**
- Select provider.
- Paste provider token/key.
- Upload 1H, 1D, and 1W screenshots.
- Analysis runs automatically and appears below.
""")
