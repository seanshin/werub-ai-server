#!/usr/bin/env python3
"""기존 werub_docs 컬렉션을 bge-m3로 재색인"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from client.rag import get_chroma, get_collection, embed_batch

def main():
    client = get_chroma()

    # 기존 컬렉션에서 데이터 백업
    old_col = client.get_collection("werub_docs")
    count = old_col.count()
    if count == 0:
        print("No documents to reindex")
        return

    print(f"Backing up {count} documents from werub_docs...")
    data = old_col.get(include=["documents", "metadatas"])

    # 기존 컬렉션 삭제
    client.delete_collection("werub_docs")
    print("Old collection deleted")

    # 새 컬렉션 생성 (bge-m3 1024차원)
    new_col = get_collection("werub_docs")

    # 50개씩 배치 재색인
    ids = data["ids"]
    docs = data["documents"]
    metas = data["metadatas"]

    for i in range(0, len(ids), 50):
        batch_ids = ids[i:i+50]
        batch_docs = docs[i:i+50]
        batch_metas = metas[i:i+50]

        embeddings = embed_batch(batch_docs)
        new_col.upsert(
            ids=batch_ids,
            embeddings=embeddings,
            documents=batch_docs,
            metadatas=batch_metas,
        )
        print(f"  Reindexed {min(i+50, len(ids))}/{len(ids)}")

    print(f"Done! {new_col.count()} documents reindexed with bge-m3")

if __name__ == "__main__":
    main()
