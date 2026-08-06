Status: Client-facing
Audience: Client
Owner: Client support team
Last verified: 2026-08-06
Canonical source: docs/CLIENT_USER_MANUAL.md
Supersedes: None

# AI Sahakar Client User Manual

**Product:** AI Sahakar official document search
**Audience:** Document search users, administrators, and client support teams
**Document status:** Living manual; update this file when user-visible behavior changes.

## 1. What FlowDocs Does

AI Sahakar lets citizens ask questions in English or Marathi about Maharashtra
cooperative laws, rules, circulars, and department guidance. It searches the
department's indexed documents and shows the references used for an answer.
Authorized administrators use the separate Operations Cockpit to manage those
documents; see the [admin guide](AI_SAHAKAR_ADMIN_USER_GUIDE.md).

FlowDocs is a document search tool, not a substitute for reviewing the source
PDF. Always verify important decisions against the referenced document.

## 2. Access and Sign-In

1. Open the organization-provided AI Sahakar URL.
2. Ask a question from the public search page; login is not required for public search.
3. Authorized staff select **Login** to open the admin console.
4. Enter your username and password.
5. Select **Logout** when finished on a shared device.

Do not share passwords or API credentials. Contact the designated support team
if access is unavailable; do not attempt to create duplicate accounts unless
your organization instructs you to do so.

## 3. Searching Documents

1. Open the AI Sahakar search page.
   The organization may use the Classic or Knowledge Workbench presentation;
   both search the same approved documents and expose the same source records.
2. Choose a suggested topic or ask one specific question in natural language.
3. Include useful terms such as department, document type, subject, date, or
   rule number.
4. Submit the question.
5. Review the answer, source summary, and contextual caution.
6. Open the listed references or choose **View sources** before relying on it.
7. If the answer is incomplete, try a narrower question or use terminology
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

Authorized document operators can create a reviewed intake from the dashboard:

1. Open the target folder.
2. Choose or drop up to 50 valid PDF files.
3. Review and edit each generated title.
4. Select **Receive Selected Files**.
5. Retry, replace, or remove any individually rejected file.
6. Select **Process Ready Documents** when every remaining file is received.
7. Wait for each document to become Searchable before relying on it in search.

### Upload Rules

- PDF files only.
- The maximum upload size is configured by the organization; the default
  documented limit is 10 MB unless support announces another limit.
- The file must be a real PDF, not only a renamed file.
- Exact duplicate file content in one intake is rejected even when the title is different.
- Use descriptive titles; titles remain editable until files are received.
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
- remove documents from search while preserving their files;
- manage users.

If an action is not visible or returns a permission message, your account does
not have that permission. Ask an administrator rather than creating another
account.

Use **Remove from Search** for superseded or historical documents. It preserves
the file and can be reversed. Permanent deletion is a separate superadmin-only
action that requires an exact confirmation and a written reason.

## 6. Language

AI Sahakar supports complete English and Marathi interface flows. Use
**English | मराठी**; your question is not changed when the interface language
changes. The answer follows the language of the question: ask in Marathi for a
Marathi answer and in English for an English answer, even if the surrounding
page is using the other language. Search quality depends on OCR quality,
document text, and the terms used in the question.

The Terms, Privacy, Disclaimer, Data Policy, and Cookie Policy links keep the
active public presentation so visitors do not unexpectedly enter the admin
interface. Policy article text is currently authored in English even when the
surrounding navigation is Marathi.

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
AI Sahakar URL:
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
