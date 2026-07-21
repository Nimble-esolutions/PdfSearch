Status: Client-facing
Audience: Client
Owner: Client support team
Last verified: 2026-07-22
Canonical source: docs/CLIENT_USER_MANUAL.md
Supersedes: None

# FlowDocs Client User Manual

**Product:** FlowDocs PDF Search
**Audience:** Document search users, administrators, and client support teams
**Document status:** Living manual; update this file when user-visible behavior changes.

## 1. What FlowDocs Does

FlowDocs lets authorized users upload PDF documents, organize them into folders,
and ask natural-language questions over the available documents. Search answers
are generated from indexed documents and include references when matching
content is available.

FlowDocs is a document search tool, not a substitute for reviewing the source
PDF. Always verify important decisions against the referenced document.

## 2. Access and Sign-In

1. Open the organization-provided FlowDocs URL.
2. Select **Login**.
3. Enter your username and password.
4. After signing in, open the dashboard to manage documents or use search.
5. Select **Logout** when finished on a shared device.

Do not share passwords or API credentials. Contact the designated support team
if access is unavailable; do not attempt to create duplicate accounts unless
your organization instructs you to do so.

## 3. Searching Documents

1. Open the search page.
2. Ask a specific question in natural language.
3. Include useful terms such as department, document type, subject, date, or
   rule number.
4. Submit the question.
5. Review the answer and open the listed references.
6. If the answer is incomplete, try a narrower question or use terminology
   that appears in the source document.

### Good Questions

- Ask one topic at a time.
- Include the relevant department or folder when known.
- Ask for a summary, section, date, rule, or comparison explicitly.

### Avoid

- Treating an answer without references as verified fact.
- Asking one question that combines unrelated subjects.
- Assuming that a missing result proves the document does not contain the
  information.

## 4. Uploading a PDF

Authorized users can upload documents from the dashboard:

1. Open the target folder.
2. Select the upload control.
3. Choose a valid PDF file.
4. Enter a clear title.
5. Submit the upload.
6. Wait for indexing to complete before searching the new document.

### Upload Rules

- PDF files only.
- The maximum upload size is configured by the organization; the default
  documented limit is 10 MB unless support announces another limit.
- The file must be a real PDF, not only a renamed file.
- Use descriptive titles and avoid duplicate titles when possible.
- Do not upload passwords, API keys, private credentials, or documents outside
  your organization’s approved data policy.

Scanned PDFs may require OCR and can take longer to become searchable.

## 5. Folders and Document Management

Folders help limit search scope and organize documents by subject or function.
Depending on your role, you may be able to:

- create folders;
- add keywords;
- upload documents;
- rename documents;
- delete documents;
- manage users.

If an action is not visible or returns a permission message, your account does
not have that permission. Ask an administrator rather than creating another
account.

Deleting a document can remove it from the application and its searchable
index. Confirm the document is no longer required before deleting it.

## 6. Language

FlowDocs supports English and Marathi where translations are available. Use the
language selector when it is visible. Search quality depends on the language,
OCR quality, document text, and the terms used in the question.

Some interface labels may remain in English while translations are being
updated.

## 7. Common Problems

### “No results” or an incomplete answer

- Check that the document is in the intended folder.
- Wait for indexing after a recent upload.
- Try the document’s exact terminology.
- Ask a narrower question.
- Open the source references.
- Contact support if several known documents cannot be found.

### Upload rejected

- Confirm the file ends in `.pdf`.
- Confirm it is below the configured size limit.
- Open the file locally to confirm it is not corrupt or password-protected.
- Try a fresh export from the source application.

### Search is slow

Large or scanned PDFs may take longer during initial indexing. Do not submit
many duplicate uploads while indexing is in progress. Contact support if the
same query remains slow after the document has been indexed.

### Login or permission failure

- Confirm the username and password.
- Confirm you are using the organization’s current URL.
- Do not retry indefinitely; contact support if the account may be locked or
  disabled.

### A page shows an error

Record the approximate time, page, action, and document title. Do not include
passwords, API keys, or confidential document contents in a support ticket.

## 8. Accuracy and Privacy Guidance

- Review source references before relying on an answer.
- Treat generated answers as assistance, not official decisions.
- Upload only approved organizational documents.
- Avoid copying confidential content into public tools or personal accounts.
- Report suspected cross-user document visibility immediately.
- Report a wrong reference, stale result, or missing document with the folder
  and document title so support can investigate indexing provenance.

## 9. Support Ticket Template

Use this template when contacting support:

```text
Date/time and timezone:
FlowDocs URL:
Username or role (do not include password):
Action attempted:
Folder/document title:
Search question (redact confidential text):
Observed result:
Expected result:
Browser/device:
Screenshot or request ID, if available:
```

## 10. Document Control

Support should update this manual when any of the following changes:

- login or role behavior;
- upload limits or supported formats;
- search and reference behavior;
- folder/document permissions;
- language support;
- public URL or support process.

Record the application release or date of the behavior change in the commit
that updates this manual. Never add secrets or private deployment details here.
