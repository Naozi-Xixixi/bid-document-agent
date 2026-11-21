from types import SimpleNamespace
from unittest.mock import patch
from app.services import vector_store

def test_qdrant_batch_preserves_query_indices():
    class FakeClient:
        def query_batch_points(self, *, collection_name, requests):
            return [SimpleNamespace(points=[SimpleNamespace(payload={'chunk_id': i + 10}, score=0.8)]) for i in range(len(requests))]
    with patch.object(vector_store,'qdrant_enabled',return_value=True), patch.object(vector_store,'_qdrant_client',return_value=FakeClient()), patch.object(vector_store,'_embed_texts',return_value=[[1.0,0.0],[0.0,1.0]]), patch.object(vector_store,'_qdrant_ensure_collection',return_value=True), patch.object(vector_store,'_qdrant_filter',return_value=None):
        result=vector_store._qdrant_search_many('test',['第一项','','第二项'],limit=5,result_id_key='id')
    assert [r[0]['id'] if r else None for r in result] == [10,None,11]
