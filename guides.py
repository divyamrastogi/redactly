"""Long-form SEO guide pages for the pdf-redact site (Phase 1, Task 1.3).

Each entry is rendered inside the shared site shell (header/footer/theme) by the
``/guides/<slug>`` route in ``app.py``. ``html_body`` is plain HTML with no Jinja
syntax. Every guide is honest, UK-flavoured, ends with one CTA to ``/``, and
includes the caveat that mortgage underwriters and UK visa applications require
unredacted statements.
"""

GUIDES = {

    'do-landlords-accept-redacted-bank-statements': {
        'title': 'Do Landlords Accept Redacted Bank Statements?',
        'meta_description': 'Can you redact a bank statement for a UK rental application? What landlords and letting agents actually need to see — and what you can safely black out.',
        'html_body': '''
<div class="guide">
    <h1>Do landlords accept redacted bank statements?</h1>
    <p class="guide-lead">Short answer: in most cases, yes — provided the statement still shows what a landlord or letting agent genuinely needs to verify. The skill is knowing what to leave visible and what to black out.</p>

    <p>When you apply to rent a property in the UK, the landlord or letting agent is usually trying to answer two questions. Can you afford the rent? And is your income roughly what you said it was on the application? A recent bank statement is one of the most common ways they check. It is also one of the most revealing documents you own — three months of spending laid bare, from your salary down to your Friday takeaway. Most people would rather not hand that over in full.</p>

    <p>The good news is that landlords rarely need, or even want, the complete picture. They want enough to feel confident that you can pay the rent each month. That means you can usually share a redacted statement and still pass referencing comfortably — as long as you do not redact the parts that actually matter.</p>

    <h2>What to leave visible</h2>
    <p>A genuinely landlord-safe statement keeps the following clear and readable:</p>
    <ul>
        <li>Your name, exactly as it appears on the tenancy application, so the agent can match the document to you.</li>
        <li>The bank's name, the statement period, and the account number or sort code — enough that the statement looks authentic and current.</li>
        <li>Opening and closing balances, so the overall shape of your finances is intact rather than patchy.</li>
        <li>Income: your salary credits, benefits, or regular transfers that demonstrate money coming in reliably.</li>
        <li>Enough routine activity that the page does not look suspiciously empty or obviously tampered with.</li>
    </ul>

    <h2>What you can safely black out</h2>
    <p>Most day-to-day spending is simply none of a landlord's business. You can generally redact:</p>
    <ul>
        <li>Discretionary spending — pubs, gambling, streaming subscriptions, clothes shopping, and the like.</li>
        <li>The names of friends or family members where you transfer money to or from them.</li>
        <li>Medical payments, charitable donations, or anything else you would prefer to keep private.</li>
        <li>Specific merchant names where the amount alone is irrelevant to proving affordability.</li>
    </ul>
    <p>The guiding principle is consistency. If you redact one transaction but leave three others visible, a sharp-eyed agent may wonder why. A statement with five readable rows and forty blacked-out ones can look evasive, even when it is entirely honest. Aim for a document that still reads like a real statement — just a tidier one.</p>

    <h2>Where redaction will not work</h2>
    <p>Redaction fits rental applications, affordability checks, and expense claims because the recipient is verifying something specific. It does not fit every situation. Mortgage underwriters and UK visa applications generally require unredacted statements — they need the complete, unmodified document, and a redacted version will almost always be rejected. Do not reach for redaction for those use cases; it will only slow you down.</p>

    <h2>Why the type of redaction matters</h2>
    <p>If you do redact a statement, make sure the text is actually gone. Drawing a black rectangle over words in a PDF — or in a free preview tool — usually just covers them visually. The underlying text is still there, and anyone who selects, copies, or extracts it can read every word. That is worse than sharing the original, because it looks hidden but is not.</p>
    <p>True redaction removes the text entirely, so copy-paste and text extraction return nothing at all. That is the only kind worth sending to a landlord. Our tool applies proper PDF redaction annotations that destroy the underlying text, not merely cover it.</p>

    <div class="guide-cta">
        <p>Ready to share your statement without handing over your whole financial life?</p>
        <a href="/">Redact your statement now &rarr;</a>
    </div>
</div>
''',
    },

    'redact-bank-statement-for-rental-application': {
        'title': 'How to Redact a Bank Statement for a Rental Application',
        'meta_description': 'Step-by-step: redact your bank statement for a UK rental application. Keep your name, balance, and income visible; hide everything else. Free tool.',
        'html_body': '''
<div class="guide">
    <h1>How to redact a bank statement for a rental application</h1>
    <p class="guide-lead">A practical, step-by-step walkthrough for preparing a bank statement for a UK rental application — what a landlord-safe statement shows, and how to produce one in a couple of minutes.</p>

    <p>Letting agents and landlords ask for bank statements because they are hard to fake and they show real cash flow. But the same qualities that make them useful for referencing also make them intrusive. The trick is to hand over a statement that answers the agent's questions — can you pay the rent, and does your income match the application — without exposing three months of personal spending.</p>

    <p>This guide walks through producing that statement with our redaction tool. The idea is simple: you upload the PDF, type in the transactions you want to keep visible, and every other transaction is permanently blacked out. The text underneath is destroyed, not just covered.</p>

    <h2>What a landlord-safe statement should show</h2>
    <p>Before you redact anything, decide what must stay visible. As a rule of thumb, keep:</p>
    <ul>
        <li>Your name and the bank's branding, so the document is clearly genuine and yours.</li>
        <li>The statement dates and account details (sort code and account number, or enough of them).</li>
        <li>Your opening and closing balances for the period.</li>
        <li>Your salary or regular income — the credits that prove you can cover the rent.</li>
    </ul>
    <p>Everything else — coffees, takeaways, subscriptions, transfers between friends — can go.</p>

    <h2>Step 1: gather your statement</h2>
    <p>Download a recent PDF statement from your bank's app or website. Most UK banks let you export a month or three as a PDF. Aim for the period the agent asked for — usually the last three months. If you have several monthly statements, you can process each one the same way.</p>

    <h2>Step 2: list the transactions to keep</h2>
    <p>On the tool's homepage, choose your provider (or leave it on auto-detect), upload your PDF, and in the keywords field enter the merchants or descriptions you want to remain visible. These are typically your salary line — for example your employer's name — and any regular income such as benefits or a standing order. Separate multiple keywords with commas.</p>
    <p>Only transactions matching your keywords survive; every other transaction is blacked out. So be deliberate: if your salary appears as a specific payroll string, include a keyword that matches it.</p>

    <h2>Step 3: redact and download</h2>
    <p>Hit redact. Within a few seconds you will get a new PDF back. Each transaction you did not whitelist has been replaced with a solid black block, and the text beneath it has been destroyed — copying and pasting into a text editor returns nothing. Your name, balances, and whitelisted transactions remain exactly as they were.</p>

    <h2>Step 4: sanity-check every page</h2>
    <p>Open the downloaded file and flick through every page. Confirm that your name and balances are intact, that your income rows are visible, and that nothing important has been accidentally hidden. If something is wrong, re-run with adjusted keywords — the original statement on your computer is untouched.</p>

    <h2>When not to redact</h2>
    <p>This approach is designed for rental applications and similar affordability checks, where the recipient is confirming something specific. It is not suitable everywhere. Mortgage underwriters and UK visa applications generally require unredacted statements in full; a redacted document will be rejected, so keep the originals for those purposes.</p>

    <div class="guide-cta">
        <p>Got your statement ready? Redact it in under a minute.</p>
        <a href="/">Redact your statement now &rarr;</a>
    </div>
</div>
''',
    },

    'redact-amex-statement-for-expense-claims': {
        'title': 'Redact an AMEX Statement for Expense Claims',
        'meta_description': 'Submit a clean AMEX statement with an expense claim — keep only reimbursable merchants, redact everything personal. How-to plus employer acceptance.',
        'html_body': '''
<div class="guide">
    <h1>How to redact an AMEX statement for expense claims</h1>
    <p class="guide-lead">Submitting a company expense claim backed by a personal American Express statement? Here is how to keep only the reimbursable merchants visible and black out everything else — cleanly and honestly.</p>

    <p>American Express statements are popular with business expenses because the rewards, the credit terms, and the detailed merchant descriptions make them well-suited to work spending. The downside is obvious: the same monthly statement that holds your client dinners and travel also holds your groceries, your streaming subscriptions, and everything else you would rather your employer not see. When finance asks for a statement to back up a claim, you do not have to hand over all of it.</p>

    <p>This guide shows how to produce a trimmed AMEX statement that keeps only the merchants you are claiming for, with every other transaction permanently removed.</p>

    <h2>What an expense-ready statement should show</h2>
    <p>A finance team processing your claim usually wants to see enough to match each line on your claim form to a real transaction. That means keeping visible:</p>
    <ul>
        <li>The merchants you are claiming — flights, hotels, client meals, software, fuel, and so on.</li>
        <li>The amounts and dates for those merchants, so they can be reconciled.</li>
        <li>Your name and the statement period, so the document is identifiable as yours.</li>
    </ul>
    <p>Everything else — personal spending, other merchants, anything not part of the claim — can be redacted.</p>

    <h2>Step 1: download your AMEX statement</h2>
    <p>Export the relevant month (or months) as a PDF from your AMEX account. If you are claiming across several months, you will process each statement separately.</p>

    <h2>Step 2: list the merchants to keep</h2>
    <p>Open the redaction tool and upload the PDF, choosing American Express as the provider (auto-detect usually works too). In the keywords field, enter the merchants you want to remain visible — for example the names of hotels, airlines, or restaurants on your claim. Separate them with commas. Only transactions whose descriptions match a keyword will survive; all others are blacked out.</p>
    <p>It is worth checking how each merchant actually appears on your statement, since the description can differ from the brand name you know. A quick scan before you redact saves a second pass.</p>

    <h2>Step 3: redact and review</h2>
    <p>Run the redaction. You will get back a PDF where every non-whitelisted transaction is covered by a solid block and the underlying text is destroyed — copying and pasting returns nothing. Your claimed merchants, with their amounts and dates, remain fully visible. Download it and check each page before you submit.</p>

    <h2>Will employers accept this?</h2>
    <p>In practice, yes — most finance teams care that they can see the specific transactions backing the claim, not that they can see your entire month. A statement that clearly shows the claimed merchants and amounts is usually perfectly acceptable, and far more professional than a screenshot with three rows circled. If your employer has a specific policy, check it, but redacted statements are widely accepted for expense claims.</p>

    <h2>Where redaction does not apply</h2>
    <p>This approach is for expense claims and similar uses where the recipient only needs certain transactions. It is not suitable for every situation. Mortgage underwriters and UK visa applications generally require unredacted statements in full, and a redacted document will be turned away. Keep your originals for those.</p>

    <div class="guide-cta">
        <p>Ready to file a clean claim?</p>
        <a href="/">Redact your AMEX statement now &rarr;</a>
    </div>
</div>
''',
    },

    'why-black-boxes-fail-pdf-redaction': {
        'title': 'Why Black Boxes Fail — Real PDF Redaction Explained',
        'meta_description': 'Drawing a black rectangle over text in a PDF does not delete it — the text is still there. Why black boxes fail and what true PDF redaction does instead.',
        'html_body': '''
<div class="guide">
    <h1>Why black boxes fail — and what real PDF redaction actually does</h1>
    <p class="guide-lead">Drawing a black rectangle over text in a PDF usually does not delete it. Here is why drawn boxes are recoverable, why that matters, and what true redaction does instead.</p>

    <p>It is one of the most common privacy mistakes people make with documents. You have a PDF with some sensitive text — a transaction, a name, a number — so you open it in a viewer, draw a solid black rectangle over the words, and save. It looks completely hidden on screen. So it must be hidden, right?</p>
    <p>Often, it is not. And the gap between "looks hidden" and "is hidden" is exactly where redaction fails.</p>

    <h2>PDFs are not pictures</h2>
    <p>The key thing to understand is that a PDF is not a flat image. It is a structured document that contains the actual text as selectable, searchable characters, plus a separate set of instructions for how to draw everything on the page. When you draw a black box over some words, you are adding a new drawing instruction on top — a filled rectangle — that visually covers the text. But the original text instruction is still in the file, sitting underneath.</p>
    <p>That means the text is still there, fully intact, and trivially readable. Select the area with your cursor and paste into a text editor. Use a copy-all-text option. Run a basic text-extraction tool. Any of these will hand back the words you thought you had hidden, because the box never touched them.</p>

    <h2>Worse: boxes can be removed</h2>
    <p>Because the black rectangle is just another object layered on top, it can be peeled off. Move or delete that rectangle — in many editors, a couple of clicks — and the text underneath reappears, perfectly legible. A redaction that can be undone by the recipient is not a redaction at all. It is decoration.</p>
    <p>This is not theoretical. There is a long history of organisations — governments, law firms, companies — releasing redacted PDFs where the sensitive text was fully recoverable because someone had drawn boxes instead of removing the content. It is a well-known, well-documented failure mode.</p>

    <h2>What true redaction does</h2>
    <p>Proper redaction does not cover the text; it removes it. Using PDF redaction annotations, the tool marks the regions to be redacted and then, crucially, applies them — which deletes the underlying text, vector graphics, and images in those regions from the content stream entirely. After that, the words are gone. Copy and paste returns nothing. Text extraction returns nothing. There is nothing underneath to recover, because it was destroyed.</p>
    <p>That is the difference between a box and a redaction. A box says do not look here. A redaction says there is nothing here. Only the second one is safe to send.</p>

    <h2>Why we do it the hard way</h2>
    <p>Destroying text takes a fraction of a second longer than drawing a box, and it is what makes the result trustworthy. Every transaction you do not whitelist is removed with proper redaction annotations and then applied, so the underlying text is genuinely gone before the file ever leaves. The output is a PDF you can share knowing the redacted content cannot be recovered by anyone, with any tool.</p>

    <h2>A note on when to redact at all</h2>
    <p>Redaction is the right tool when the recipient only needs part of a document — a rental application, an expense claim, an affordability check. It is not right everywhere. Mortgage underwriters and UK visa applications generally require unredacted statements in full, and a redacted file will be rejected outright. Match the tool to the situation.</p>

    <div class="guide-cta">
        <p>Want redaction that actually removes the text?</p>
        <a href="/">Redact your statement now &rarr;</a>
    </div>
</div>
''',
    },

}
