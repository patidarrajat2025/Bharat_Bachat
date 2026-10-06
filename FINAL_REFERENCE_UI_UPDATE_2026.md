# Bharat Bachat — Reference UI Update 2026-10

Base: `bharat-bachat-2026-FINAL-production-figma-ui-ux-fullstack-polished.zip`

Implemented from the supplied reference screenshots:

- Login rebuilt as a single mobile reference composition: scenery hero, in-hero app logo/brand, Hindi/English segmented toggle, tagline, overlapping glass login card, compact fields and emerald CTA.
- Authenticated header standardized to a navy Bharat Bachat header with app logo, brand wordmark, circular profile control and consistent styling across Member, Group Admin and Super Admin screens.
- Group Dashboard vault header now shows the tenant/group logo and group name.
- Financial semantic colors standardized: inflow/credit/contribution/income/collected = `#E6F4EA`; outflow/debit/expense/repayment/penalty = `#FCE8E6`.
- Analytics bars narrowed with consistent rounded tops and transparent plotting surfaces; existing ResponsiveContainer height remains 260px and glass tooltip remains active.
- Profile menu now uses a full-viewport click-away layer; any click outside the menu dismisses it immediately. Notifications remain available inside the profile menu.
- Member directory and Member-Wise Register use progressive disclosure: compact row first, financial/detail cards only after explicit expansion; full Member Profile remains available through the dedicated action.
- Global route scroll reset added so navigation opens the next screen at the top and resets marked nested scroll containers.
- Mobile bottom-navigation clearance and existing responsive behavior retained.

No backend/data model changes were required for this visual/interaction pass.
