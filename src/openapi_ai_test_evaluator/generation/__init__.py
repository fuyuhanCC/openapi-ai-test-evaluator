"""Provider-independent test-case generation boundaries."""

from openapi_ai_test_evaluator.generation.case_admission import (
    GeneratedCaseAdmission,
    ProviderOutputAdmissionError,
    admit_generated_cases,
)
from openapi_ai_test_evaluator.generation.deepseek_provider import (
    DEEPSEEK_API_KEY_ENV,
    DEFAULT_DEEPSEEK_BASE_URL,
    DeepSeekProvider,
    DeepSeekProviderConfigError,
)
from openapi_ai_test_evaluator.generation.fake_provider import FakeProvider
from openapi_ai_test_evaluator.generation.openapi_context import build_openapi_context
from openapi_ai_test_evaluator.generation.orchestrator import (
    GenerationAttempt,
    generate_test_case_batch,
)
from openapi_ai_test_evaluator.generation.pipeline import generate_cases_from_openapi
from openapi_ai_test_evaluator.generation.prompt_builder import (
    PROMPT_VERSION,
    SUPPORTED_PROMPT_VERSIONS,
    PromptBuildError,
    build_provider_request,
)
from openapi_ai_test_evaluator.generation.provider import (
    LLMProvider,
    LLMProviderError,
    ProviderRequest,
    ProviderResponse,
)
from openapi_ai_test_evaluator.generation.schemathesis_adapter import (
    AdaptationRejection,
    AdaptationRejectionCode,
    CapturedGenerationMode,
    CapturedPhase,
    CapturedSchemathesisCase,
    SchemathesisCaseAdaptation,
    adapt_schemathesis_case,
)
from openapi_ai_test_evaluator.generation.schemathesis_batch import (
    SCHEMATHESIS_ADAPTER_VERSION,
    RejectedSchemathesisCase,
    SchemathesisBatchAdaptation,
    SchemathesisBatchCollector,
)
from openapi_ai_test_evaluator.generation.schemathesis_capture import (
    SchemathesisCaseCapture,
    capture_schemathesis_case,
)
from openapi_ai_test_evaluator.generation.schemathesis_generator import (
    SchemathesisGenerationConfig,
    generate_schemathesis_batch,
)

__all__ = [
    "DEFAULT_DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_KEY_ENV",
    "AdaptationRejection",
    "AdaptationRejectionCode",
    "CapturedGenerationMode",
    "CapturedPhase",
    "CapturedSchemathesisCase",
    "DeepSeekProvider",
    "DeepSeekProviderConfigError",
    "FakeProvider",
    "GenerationAttempt",
    "GeneratedCaseAdmission",
    "LLMProvider",
    "LLMProviderError",
    "ProviderRequest",
    "ProviderResponse",
    "ProviderOutputAdmissionError",
    "PROMPT_VERSION",
    "RejectedSchemathesisCase",
    "SCHEMATHESIS_ADAPTER_VERSION",
    "SUPPORTED_PROMPT_VERSIONS",
    "SchemathesisCaseAdaptation",
    "SchemathesisCaseCapture",
    "SchemathesisGenerationConfig",
    "SchemathesisBatchAdaptation",
    "SchemathesisBatchCollector",
    "PromptBuildError",
    "adapt_schemathesis_case",
    "admit_generated_cases",
    "build_openapi_context",
    "build_provider_request",
    "capture_schemathesis_case",
    "generate_cases_from_openapi",
    "generate_schemathesis_batch",
    "generate_test_case_batch",
]
