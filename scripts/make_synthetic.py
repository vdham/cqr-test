"""
Deliberately problematic transcripts so every signal has something to fire on.
ABCD conversations are crowdworker-polite: risk flags, escalation, and wrong
answers are rare there. These fill the gap. Each one is labeled with what it is
designed to exercise (metadata.expect) so the demo can show the reviewer
catching it, and so we have a tiny eval set.

References are taken from ABCD guidelines where a real rule exists (missing-item
7-day rule, refund-method options), so correctness is judged against the same
policy text the ABCD agents were given.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cqr.loader import GuidelineIndex, dump_jsonl  # noqa: E402
from cqr.schema import Transcript, Turn  # noqa: E402

ABCD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/abcd")
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/synthetic.jsonl")

gi = GuidelineIndex.load(ABCD / "guidelines.json")
REF_MISSING = gi.reference_for("shipping_issue", "missing")
REF_REFUND = gi.reference_for("product_defect", "refund_initiate")
REF_SHIP_STATUS = gi.reference_for("shipping_issue", "status")
REF_PASSWORD = gi.reference_for("account_access", "recover_password")


def T(id_, intent, ref, expect, *pairs):
    turns = [Turn(idx=i, speaker=s, text=t) for i, (s, t) in enumerate(pairs)]
    return Transcript(id=id_, source="synthetic", intent=intent, reference=ref, turns=turns,
                      metadata={"expect": expect})


C, A = "customer", "agent"

transcripts = [

    # 1. Happy on the surface, wrong on the facts. Policy: <7 days => tell them to wait.
    T("syn-01-wrong-but-happy", "shipping_issue/missing", REF_MISSING,
      "correctness=contradicted; resolution looks resolved; sentiment up; NO risk flag by itself",
      (A, "Hi, thanks for contacting AcmeBrand support. How can I help?"),
      (C, "My order says delivered but nothing came. Order 4481920."),
      (A, "Sorry about that! Can I get your full name?"),
      (C, "Dana Ortiz. It said delivered yesterday, so I've been waiting 3 days total since it shipped."),
      (A, "Thanks Dana. I'll go ahead and ship a replacement right now. Can you confirm your address?"),
      (C, "12 Pine St, Denver CO 80203. Wow, that was easy, thank you!"),
      (A, "Done, a new pair of Guess jeans is on its way. Anything else?"),
      (C, "Nope, great service!")),

    # 2. Unauthorized promise. Policy: refund methods are gift card / add value / paper check / credit card; no timeline promise in guidelines.
    T("syn-02-unauthorized-promise", "product_defect/refund_initiate", REF_REFUND,
      "risk=unauthorized_promise; correctness=unverifiable or contradicted (timeline not in policy)",
      (A, "Hello! How can I help today?"),
      (C, "I need a refund for a jacket that came ripped. Username jlee88, order 77120034."),
      (A, "I'm so sorry to hear that. Let me pull up your account. Full name please?"),
      (C, "Jordan Lee."),
      (A, "Got it. I've initiated a full refund of $120 to your credit card. You'll see it in your account by tomorrow morning, guaranteed."),
      (C, "Tomorrow? That's great, thanks."),
      (A, "You're welcome! Have a great day.")),

    # 3. Resolved but high effort: repeats, transfer, re-explains.
    T("syn-03-resolved-high-effort", "account_access/recover_password", REF_PASSWORD,
      "resolution=resolved; effort=high; interaction_quality=low",
      (A, "Support here."),
      (C, "Hi, I can't log in. It says my password is wrong but I haven't changed it."),
      (A, "Username?"),
      (C, "mpatel_21"),
      (A, "And your name?"),
      (C, "Meera Patel. I gave my username already."),
      (A, "I need both. Hold on."),
      (A, "Actually this needs the account team, transferring you."),
      (A, "Hi, this is the account team. What's the issue?"),
      (C, "As I told the last person: I can't log in, password rejected. Username mpatel_21, Meera Patel."),
      (A, "OK. What's your phone number on file?"),
      (C, "555-0142. Do you need my zip too? It's 94110."),
      (A, "I've reset it. Check your email."),
      (C, "Got it, it works now. That took way longer than it should have.")),

    # 4. Escalation: cancel + competitor + social threat. Agent deflects.
    T("syn-04-churn-and-deflection", "order_issue/status_delivery_time", REF_SHIP_STATUS,
      "risk=churn_signal+social_amplification; interaction_quality=low; resolution=unresolved",
      (A, "Hi, how can I help?"),
      (C, "This is the third time my order has been late. I'm paying for premium shipping and it's a joke."),
      (A, "Shipping times are handled by the carrier, not us."),
      (C, "I don't care who handles it. I paid you. Either fix it or I'm cancelling and going to Zappos."),
      (A, "That's your choice. Is there anything else?"),
      (C, "Unbelievable. I'm posting this whole chat on Twitter."),
      (A, "OK.")),

    # 5. PII mishandling: agent asks for full card number in chat.
    T("syn-05-pii", "manage_account/manage_payment_method", None,
      "risk=pii_mishandling; correctness=unverifiable (no reference)",
      (A, "Welcome to AcmeBrand. How can I help?"),
      (C, "I want to update the card on my account."),
      (A, "Sure. Please type the full 16-digit card number, expiry, and the CVV here in the chat and I'll update it."),
      (C, "Uh, is that safe? OK: 4111 1111 1111 1111, 09/28, 123."),
      (A, "Updated. Anything else?"),
      (C, "No, thanks.")),

    # 6. Good conversation: angry -> calm, correct, resolved, low effort. Policy: >=7 days => reship.
    T("syn-06-exemplary", "shipping_issue/missing", REF_MISSING,
      "everything good: correctness=supported, resolution=resolved, effort=low, quality=high, sentiment up, no flags",
      (A, "Hi, this is Sam with AcmeBrand. How can I help?"),
      (C, "I've been waiting TWELVE days for boots that were marked delivered. This is ridiculous."),
      (A, "Twelve days with nothing to show for it is not okay, and I'm sorry you've had to chase this. I can fix it right now. Can I get your full name so I can pull up the order?"),
      (C, "Priya Nair."),
      (A, "Thanks Priya. I see the order. Since it's been more than a week, I'm going to ship a new pair at no cost. Can you confirm the address you'd like it sent to?"),
      (C, "88 Lake Rd, Austin TX 78701."),
      (A, "Perfect. A new pair of Timberland boots is on its way to 88 Lake Rd, and I've emailed you the tracking number. If anything looks off with the tracking in the next couple of days, reply to that email and it will come straight to me."),
      (C, "OK. Thank you, that's actually really helpful."),
      (A, "Glad I could sort it. Take care, Priya.")),

    # 7. Deferred with a clear owner: not resolved, but handled well.
    T("syn-07-deferred-with-owner", "purchase_dispute/mistimed_billing_never_bought", None,
      "resolution=deferred_with_owner; quality=high; effort=low",
      (A, "Hi, how can I help you today?"),
      (C, "There's a $210 charge on my card from you and I never bought anything."),
      (A, "That would worry me too. Let's get to the bottom of it. Can I have your full name and the last four digits of the card?"),
      (C, "Alex Kim, ending 4402."),
      (A, "Thank you. I can see the charge but I'm not able to reverse it from here; it needs our billing team. I've opened case #55821 with them, marked it urgent, and you'll get an email from billing within 2 business days. If you don't hear by Thursday, reply to the case email and it escalates automatically."),
      (C, "OK. Two days is fine as long as it actually gets handled."),
      (A, "It will. I've also added a note that you have not made a purchase, so they start from that.")),

    # 8. Agent hostile.
    T("syn-08-abusive-agent", "storewide_query/policy_1", None,
      "risk=abusive_agent; quality=low",
      (A, "Yes?"),
      (C, "Hi, quick question, what's your return window?"),
      (A, "It's on the website. Did you look?"),
      (C, "I did, I couldn't find it."),
      (A, "It's literally the first link under Help. 30 days. Anything else or are we done?"),
      (C, "...no, that's it.")),

    # 9. Wrong the other direction: customer at 10 days told to keep waiting. Policy: >=7 days => reship.
    T("syn-09-wrong-refusal", "shipping_issue/missing", REF_MISSING,
      "correctness=contradicted; resolution=unresolved; sentiment down",
      (A, "Hello, how can I help?"),
      (C, "My package was marked delivered 10 days ago and I still don't have it."),
      (A, "Sorry to hear that. Sometimes packages are marked delivered early. Please give it a few more days and check with neighbors."),
      (C, "It's been TEN days. I already checked with neighbors."),
      (A, "I understand. Our policy is to allow time for delivery. Please check back if it hasn't arrived by next week."),
      (C, "This is useless.")),
]

dump_jsonl(transcripts, OUT)
print(f"wrote {len(transcripts)} synthetic transcripts to {OUT}")
