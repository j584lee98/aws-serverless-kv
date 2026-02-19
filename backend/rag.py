"""
RAG (Retrieval-Augmented Generation) helpers.
Handles embedding generation, cosine similarity, and context retrieval.
"""
import json
import math
import boto3.dynamodb.conditions

from config import bedrock_runtime, chunks_table

# ── Constants ──────────────────────────────────────────────────────────────────
EMBEDDING_MODEL_ID  = 'amazon.titan-embed-text-v2:0'
RAG_TOP_K           = 5      # chunks injected into the prompt
RAG_MAX_CHARS       = 4000   # safety cap on total context characters
RAG_SCORE_THRESHOLD = 0.30   # ignore chunks below this cosine similarity


def _embed_text(text: str) -> list:
    """
    Generate a 256-dim vector for *text* using Titan Text Embeddings v2.
    Returns an empty list if embedding is unavailable (graceful degradation).
    """
    try:
        body = json.dumps({'inputText': text, 'dimensions': 256, 'normalize': True})
        resp = bedrock_runtime.invoke_model(
            modelId=EMBEDDING_MODEL_ID,
            body=body,
            contentType='application/json',
            accept='application/json',
        )
        return json.loads(resp['body'].read())['embedding']
    except Exception as e:
        print(f'Embedding error: {e}')
        return []


def _cosine_similarity(a: list, b: list) -> float:
    """Cosine similarity between two equal-length vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot    = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def retrieve_context(user_id: str, query: str, top_k: int = RAG_TOP_K) -> list:
    """
    Embed *query*, fetch all chunks for *user_id* from DynamoDB, rank by
    cosine similarity, and return the top-k chunk dicts above the score
    threshold.

    Each returned dict has keys: chunk_text, filename, chunk_index, score.
    Returns an empty list when the chunks table is unavailable or when the
    user has no documents indexed.
    """
    if not chunks_table:
        return []

    query_embedding = _embed_text(query)
    if not query_embedding:
        return []

    # Load all chunks for this user (pagination-aware)
    chunks = []
    last_key = None
    while True:
        kwargs = {
            'KeyConditionExpression': (
                boto3.dynamodb.conditions.Key('user_id').eq(user_id)
            )
        }
        if last_key:
            kwargs['ExclusiveStartKey'] = last_key
        response = chunks_table.query(**kwargs)
        chunks.extend(response.get('Items', []))
        last_key = response.get('LastEvaluatedKey')
        if not last_key:
            break

    if not chunks:
        return []

    # Score every chunk; apply relevance threshold
    scored = []
    for item in chunks:
        try:
            stored_emb = json.loads(item.get('embedding', '[]'))
        except (json.JSONDecodeError, TypeError):
            continue
        score = _cosine_similarity(query_embedding, stored_emb)
        if score < RAG_SCORE_THRESHOLD:
            continue
        scored.append({
            'chunk_text':  item.get('chunk_text', ''),
            'filename':    item.get('filename', ''),
            'chunk_index': int(item.get('chunk_index', 0)),
            'score':       score,
        })

    # Return top-k by descending score
    scored.sort(key=lambda x: x['score'], reverse=True)
    return scored[:top_k]


def build_rag_prompt(user_message: str, context_chunks: list) -> str:
    """
    Wrap the user message with retrieved context chunks.
    Falls back to the bare user message when no context is available.
    """
    if not context_chunks:
        return user_message

    context_parts = []
    total_chars   = 0
    for chunk in context_chunks:
        text = chunk['chunk_text'].strip()
        if total_chars + len(text) > RAG_MAX_CHARS:
            break
        context_parts.append(
            f"[Source: {chunk['filename']}, chunk {chunk['chunk_index']}]\n{text}"
        )
        total_chars += len(text)

    context_block = '\n\n---\n\n'.join(context_parts)
    return (
        f"Use the following excerpts from the user's documents to help answer "
        f"their question. If the excerpts are not relevant, answer from your "
        f"general knowledge instead.\n\n"
        f"CONTEXT:\n{context_block}\n\n"
        f"USER QUESTION:\n{user_message}"
    )
