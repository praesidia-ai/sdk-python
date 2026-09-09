import json
from pathlib import Path
import pytest
from praesidia.telemetry import gen_ai_span, parse_traceparent, GENAI_SEMCONV_VERSION

FIXTURE = json.loads((Path(__file__).parents[1] / 'test-fixtures/genai-telemetry-v1.json').read_text())

def test_parentage_and_provider():
    first = gen_ai_span(FIXTURE['agentName'], traceparent=FIXTURE['traceparent'], system=FIXTURE['provider'], task_id=FIXTURE['taskId'], action_id=FIXTURE['actionId'])
    second = gen_ai_span(FIXTURE['agentName'], traceparent=FIXTURE['traceparent'])
    assert GENAI_SEMCONV_VERSION == FIXTURE['semanticConventions']
    assert first['traceId'] == second['traceId'] == FIXTURE['traceId']
    assert first['spanId'] != second['spanId']
    assert first['parentSpanId'] == FIXTURE['parentSpanId']
    assert first['flags'] == 1
    attrs = {item['key']: item['value'] for item in first['attributes']}
    assert attrs['gen_ai.provider.name']['stringValue'] == FIXTURE['provider']
    assert attrs['praesidia.action.id']['stringValue'] == FIXTURE['actionId']

@pytest.mark.parametrize('parent', FIXTURE['invalidTraceparents'])
def test_invalid_parent_starts_root(parent):
    assert parse_traceparent(parent) is None
    assert 'parentSpanId' not in gen_ai_span('agent', traceparent=parent)

@pytest.mark.parametrize('key', FIXTURE['sensitiveAttributes'])
def test_content_and_secrets_default_off(key):
    span = gen_ai_span('agent', extra_attributes=[{'key': key, 'value': {'stringValue': 'private fixture data'}}])
    assert 'private fixture data' not in json.dumps(span)

def test_capture_requires_redaction():
    attrs = [{'key': 'gen_ai.input.messages', 'value': {'stringValue': 'private fixture data'}}]
    with pytest.raises(ValueError, match='requires redact_content'):
        gen_ai_span('agent', capture_content=True, extra_attributes=attrs)
    span = gen_ai_span('agent', capture_content=True, redact_content=lambda _: '[redacted]', extra_attributes=attrs)
    assert '[redacted]' in json.dumps(span)
    assert 'private fixture data' not in json.dumps(span)
    with pytest.raises(ValueError, match='16384'):
        gen_ai_span('agent', capture_content=True, redact_content=lambda _: 'x' * 16385, extra_attributes=attrs)

@pytest.mark.parametrize('key', ['gen_ai.agent.id', 'gen_ai.provider.name', 'praesidia.task.id', 'praesidia.action.id'])
def test_reserved_identity_cannot_be_replaced(key):
    with pytest.raises(ValueError, match='reserved'):
        gen_ai_span('agent', extra_attributes=[{'key': key, 'value': {'stringValue': 'foreign'}}])


def test_vendored_telemetry_contract_matches_sibling_when_available():
    sibling = Path(__file__).parents[2] / 'shared/contracts/genai-telemetry-v1.json'
    if sibling.exists():
        assert json.loads(sibling.read_text()) == FIXTURE
