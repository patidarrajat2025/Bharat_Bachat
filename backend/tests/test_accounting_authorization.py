import unittest

from fastapi import HTTPException
from app.api.group import _assert_accounting_view_access


class AccountingAuthorizationTests(unittest.TestCase):
    def test_member_can_access_only_personal_aggregate_view(self):
        _assert_accounting_view_access("member", "profit")
        for view in ("expenses", "outflows", "closing", "interest", "active-loans", "kist", "register"):
            with self.subTest(view=view):
                with self.assertRaises(HTTPException) as ctx:
                    _assert_accounting_view_access("member", view)
                self.assertEqual(ctx.exception.status_code, 403)

    def test_admin_roles_can_access_group_accounting_views(self):
        for role in ("group_admin", "super_admin"):
            _assert_accounting_view_access(role, "expenses")

    def test_unknown_role_is_denied(self):
        with self.assertRaises(HTTPException) as ctx:
            _assert_accounting_view_access("guest", "profit")
        self.assertEqual(ctx.exception.status_code, 403)
