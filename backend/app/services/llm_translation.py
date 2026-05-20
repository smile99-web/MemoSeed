import json
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class LlmTranslationSettings:
    provider: str
    base_url: str
    model: str
    api_key: str | None = None


DEFAULT_LLM_TRANSLATION_SETTINGS = LlmTranslationSettings(provider="ollama", base_url="http://localhost:11434", model="phi4-mini")


def needs_translation(chinese_text: str) -> bool:
    return not chinese_text.strip() or chinese_text.strip() == "待补充"


def translate_english_to_chinese(english_text: str, settings: LlmTranslationSettings) -> str:
    prompt = (
        "Translate the following English word, phrase, or sentence into concise Simplified Chinese. "
        "Return only the Chinese translation, with no explanation, quotes, markdown, or extra text.\n\n"
        f"English: {english_text.strip()}"
    )
    response = call_llm_generate(settings, prompt)
    translated_text = response.strip().strip('"“”')
    if not translated_text:
        raise ValueError("LLM translation returned empty text")
    return translated_text


def generate_learning_text(prompt: str, settings: LlmTranslationSettings) -> str:
    response = call_llm_generate(settings, prompt)
    generated_text = response.strip().strip('"“”')
    if not generated_text:
        raise ValueError("LLM generation returned empty text")
    return generated_text


def call_llm_generate(settings: LlmTranslationSettings, prompt: str) -> str:
    provider = settings.provider.strip().lower()
    if provider in {"ollama", "local"}:
        return call_ollama_generate(settings.base_url, settings.model, prompt)
    if provider in {"deepseek", "openai"}:
        return call_openai_chat_completion(settings.base_url, settings.model, settings.api_key, prompt)
    raise ValueError(f"Unsupported LLM provider: {settings.provider}")


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
    with urlopen(request, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))

    generated_text = body.get("response")
    if not isinstance(generated_text, str):
        raise ValueError("Invalid Ollama response")
    return generated_text


def call_openai_chat_completion(base_url: str, model: str, api_key: str | None, prompt: str) -> str:
    if not base_url.strip():
        raise ValueError("LLM base URL is required")
    if not model.strip():
        raise ValueError("LLM model is required")
    if not api_key or not api_key.strip():
        raise ValueError("DeepSeek API key is required")

    url = base_url.rstrip("/") + "/chat/completions"
    payload = json.dumps(
        {
            "model": model.strip(),
            "messages": [
                {"role": "system", "content": "You translate English into concise Simplified Chinese."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
            "stream": False,
        }
    ).encode("utf-8")
    request = Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {api_key.strip()}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise ValueError(f"LLM translation failed: {base_url}: {exc}") from exc

    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValueError("Invalid OpenAI-compatible response")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    generated_text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(generated_text, str):
        raise ValueError("Invalid OpenAI-compatible response")
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
