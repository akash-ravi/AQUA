package com.example.jarvis.device

object ConfirmationGuard {
    private val payment = Regex("\\b(place order|order now|pay|buy|purchase|confirm order|confirm payment|confirm purchase|submit order|submit payment|donate|send money|transfer money)\\b", RegexOption.IGNORE_CASE)
    private val possible = Regex("\\b(subscribe|start trial|start free trial|book|reserve|confirm booking|request ride|transfer)\\b", RegexOption.IGNORE_CASE)
    private val effect = Regex("\\b(send|call|dial|post|publish)\\b", RegexOption.IGNORE_CASE)
    private val commerce = Regex("\\b(payment|checkout|check out|beneficiary|bank transfer|subscription|billing|fare|charge|fee|per minute|premium sms|paid)\\b", RegexOption.IGNORE_CASE)
    private val free = Regex("\\b(free|no charge|no cost|zero cost)\\b", RegexOption.IGNORE_CASE)
    private val money = Regex("(?:[$€£₹]|\\b(?:USD|EUR|GBP|INR|CAD|AUD)\\s*)\\s*\\d", RegexOption.IGNORE_CASE)
    private val branding = Regex("\\bbuy \\d+\\b.*\\bget\\b|\\b(google|apple|samsung) pay\\b|\\bpay with\\b|\\bpaypal\\b", RegexOption.IGNORE_CASE)
    private val browsing = Regex("\\b(learn more|compare|tech specs|view pricing|see pricing|(?:choose|select) (?:your |a )?(?:model|color|colour|size|storage|finish))\\b", RegexOption.IGNORE_CASE)
    private val checkout = Regex("\\b(checkout|check out|total|amount due|order summary|payment method|billing|shipping address|delivery address|place order|pay now|confirm purchase|confirm payment|confirm order|one.click|1.click)\\b", RegexOption.IGNORE_CASE)

    fun needsConfirmation(label: String, context: String = label): Boolean {
        val action = branding.replace(label, " ")
        if (label.trim().equals("Buy", ignoreCase = true) && browsing.containsMatchIn(context)
            && !checkout.containsMatchIn(context)) return false
        val amounts = Regex("(?:[$€£₹]|\\b(?:USD|EUR|GBP|INR|CAD|AUD)\\s*)\\s*([0-9,]+)(?:\\.(\\d{2}))?", RegexOption.IGNORE_CASE).findAll(context).toList()
        if (amounts.isNotEmpty() && amounts.all { it.groupValues[1].replace(",", "").toLongOrNull() == 0L && it.groupValues[2].ifEmpty { "0" }.toInt() == 0 }
            && Regex("\\b(total|amount due|amount to send|you pay)\\b", RegexOption.IGNORE_CASE).containsMatchIn(context)
            && !Regex("renew|recurring|trial|per month|per year", RegexOption.IGNORE_CASE).containsMatchIn(context)) return false
        if (payment.containsMatchIn(action)) return true
        if (Regex("\\b(confirm|submit|continue|complete|finish)\\b", RegexOption.IGNORE_CASE).containsMatchIn(action)
            && commerce.containsMatchIn(context) && money.containsMatchIn(context)) return true
        if (!possible.containsMatchIn(action) && !effect.containsMatchIn(action)) return false
        if (money.containsMatchIn(action)) return true
        if (commerce.containsMatchIn(context) && (money.containsMatchIn(context) ||
                Regex("\\b(paid|fee|charge|per minute|premium sms)\\b", RegexOption.IGNORE_CASE).containsMatchIn(context))) return true
        if (Regex("\\btrial\\b", RegexOption.IGNORE_CASE).containsMatchIn(context) &&
            Regex("\\b(subscribe|trial)\\b", RegexOption.IGNORE_CASE).containsMatchIn(action)) return true
        if (free.containsMatchIn(action) || (free.containsMatchIn(context) && !money.containsMatchIn(context))) return false
        return possible.containsMatchIn(action)
    }
}
