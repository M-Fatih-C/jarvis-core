# Creating Firestore Vector Index for Jarvis Memory

Firestore supports vector search across embeddings using Google Cloud CLI or Firebase console.

## 1. Vector Index Specification
- Collection: `memories` (or collectionGroup: `memories`)
- Field: `embedding`
- Dimension: 384 (for `intfloat/multilingual-e5-small`)
- Distance measure: `COSINE`

## 2. Using Google Cloud CLI (`gcloud`)

```bash
gcloud firestore indexes composite create \
  --project="YOUR_FIREBASE_PROJECT_ID" \
  --collection-group=memories \
  --query-scope=COLLECTION \
  --field-config field-path=embedding,vector-config='{"dimension":"384","flat": "{}"}'
```

Or for nearest-neighbor approximate vector index:

```bash
gcloud alpha firestore indexes composite create \
  --project="YOUR_FIREBASE_PROJECT_ID" \
  --collection-group=memories \
  --query-scope=COLLECTION \
  --field-config field-path=embedding,vector-config='{"dimension":"384","flat":{}}'
```

> [!NOTE]
> During Milestone 2 development and testing, all vector matching is performed deterministically inside the local environment without incurring cloud billing.
