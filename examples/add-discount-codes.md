# Add discount codes at checkout

**Critical:** no
**Risk:** money

**Size** M

**Story.** As a shopper, I want to enter a discount code at checkout, so that the promotion price is applied.

**Acceptance criteria**
1. Given a valid code, then the order total shows the discounted price.
2. Given an expired code, then the checkout page says so and the price does not change.
3. Given a code used up to its limit, then it is refused.
4. Given a discount, then the invoice shows the original price and the discount.

**Depends on** #12, #15.
**Open question** Can two codes be combined? (Product owner decides.)
