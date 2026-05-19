import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LlmTranslationSettings:
    base_url: str
    model: str


DEFAULT_LLM_TRANSLATION_SETTINGS = LlmTranslationSettings(base_url="http://localhost:11434", model="phi4-mini")


def needs_translation(chinese_text: str) -> bool:
    return not chinese_text.strip() or chinese_text.strip() == "待补充"


def translate_english_to_chinese(english_text: str, settings: LlmTranslationSettings) -> str:
    prompt = (
        "Translate the following English word, phrase, or sentence into concise Simplified Chinese. "
        "Return only the Chinese translation, with no explanation, quotes, markdown, or extra text.\n\n"
        f"English: {english_text.strip()}"
    )
    response = call_ollama_generate(settings.base_url, settings.model, prompt)
    translated_text = response.strip().strip('"“”')
    if not translated_text:
        raise ValueError("LLM translation returned empty text")
    return translated_text


def call_ollama_generate(base_url: str, model: str, prompt: str) -> str:
    if not base_url.strip():
        raise ValueError("LLM base URL is required")
    if not model.strip():
        raise ValueError("LLM model is required")

    errors: list[str] = []
    for candidate_base_url in get_candidate_base_urls(base_url.strip()):
        try:
            return request_ollama_generate(candidate_base_url, model.strip(), prompt)
        except (HTTPError, URLError, TimeoutError, ValueError) as exc:
            errors.append(f"{candidate_base_url}: {exc}")

    raise ValueError("LLM translation failed: " + "; ".join(errors))


def request_ollama_generate(base_url: str, model: str, prompt: str) -> str:
    url = base_url.rstrip("/") + "/api/generate"
    payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode("utf-8")
    request = Request(url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=60) as response:
        body = json.loads(response.read().decode("utf-8"))

    generated_text = body.get("response")
    if not isinstance(generated_text, str):
        raise ValueError("Invalid Ollama response")
    return generated_text


def get_candidate_base_urls(base_url: str) -> list[str]:
    parsed_url = urlparse(base_url)
    if parsed_url.hostname not in {"localhost", "127.0.0.1"}:
        return [base_url]

    host_docker_internal = urlunparse(parsed_url._replace(netloc=replace_hostname(parsed_url.netloc, "host.docker.internal")))
    return [base_url, host_docker_internal]


def replace_hostname(netloc: str, hostname: str) -> str:
    if "@" in netloc:
        auth, host = netloc.rsplit("@", 1)
        return f"{auth}@{replace_hostname(host, hostname)}"

    if ":" in netloc:
        _, port = netloc.rsplit(":", 1)
        return f"{hostname}:{port}"

    return hostname
