"""Synthetic fixtures only. Never read local credentials or the real database."""
import pytest


@pytest.fixture
def payload():
    return {'status': 200, 'prompt_options': {'prompt_length': 'medium', 'parallel_queries': 1},
            'data': [{'id': 'synthetic-model-1', 'name': '合成测试模型', 'slug': 'synthetic-1',
                      'model_creator': {'id': 'synthetic-creator', 'name': '测试厂商'},
                      'evaluations': {'artificial_analysis_intelligence_index': 0, 'gpqa': 0.5},
                      'pricing': {'price_1m_input_tokens': 0, 'price_1m_output_tokens': 2},
                      'median_output_tokens_per_second': 10,
                      'median_time_to_first_token_seconds': 0}]}
