# AgenticRAG Platform — Frequently Asked Questions

## General

**Q: What is AgenticRAG?**
A: AgenticRAG is an AI-powered document search and question-answering platform that uses Retrieval Augmented Generation (RAG) to provide accurate answers from your documents.

**Q: What file formats are supported?**
A: PDF, DOCX, TXT, MD, and HTML files are supported. Maximum file size is 50MB.

**Q: How long does document ingestion take?**
A: Typically 30–120 seconds depending on document size and complexity.

## Search

**Q: How does semantic search work?**
A: Documents are split into chunks and converted into vector embeddings using Azure OpenAI text-embedding-ada-002. Queries are also embedded and matched against document chunks using cosine similarity.

**Q: How many documents can I upload?**
A: The Basic tier supports up to 1,000 documents and 10,000 chunks.

**Q: Can I search across multiple documents?**
A: Yes, all uploaded documents are indexed together and searched simultaneously.

## Security

**Q: Where is my data stored?**
A: All data is stored in your Azure subscription. No data leaves your tenant.

**Q: Who can access my documents?**
A: Only authenticated users in your Azure AD tenant can access documents.

**Q: Are documents encrypted?**
A: Yes, all documents are encrypted at rest (AES-256) and in transit (TLS 1.2+).

## Billing

**Q: What is the pricing model?**
A: Pricing is based on number of documents indexed and number of queries per month.

**Q: Is there a free tier?**
A: Yes, the free tier includes 100 documents and 1,000 queries per month.
