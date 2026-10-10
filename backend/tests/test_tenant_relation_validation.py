import unittest
from app.tenant_relations import scoped_entity_query, relation_matches


class TenantRelationValidationTests(unittest.TestCase):
    def test_entity_queries_always_include_tenant(self):
        query = scoped_entity_query('tenant-a', 'member-id')
        self.assertEqual(query, {'_id': 'member-id', 'tenant_id': 'tenant-a'})

    def test_share_query_is_scoped_to_tenant_and_member(self):
        query = scoped_entity_query('tenant-a', 'share-id', member_id='member-a')
        self.assertEqual(query, {'_id': 'share-id', 'tenant_id': 'tenant-a', 'member_id': 'member-a'})

    def test_cross_tenant_relation_is_rejected_even_if_row_is_returned(self):
        self.assertFalse(relation_matches({'tenant_id':'tenant-b'}, 'tenant-a'))

    def test_cross_member_share_is_rejected(self):
        self.assertFalse(relation_matches({'tenant_id':'tenant-a','member_id':'member-b'}, 'tenant-a', member_id='member-a'))

    def test_valid_relation_matches(self):
        self.assertTrue(relation_matches({'tenant_id':'tenant-a','member_id':'member-a'}, 'tenant-a', member_id='member-a'))


if __name__ == '__main__':
    unittest.main()
