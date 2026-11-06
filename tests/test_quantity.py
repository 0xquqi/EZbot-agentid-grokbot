"""Quantity default: omitted mint size fills to the capped maximum."""
import json
import tempfile
import unittest
from pathlib import Path

from runtime.app import BotRuntime
from runtime.common import digest
from runtime.mint import MintCoordinator
from runtime.orders import OrderStore, intent, order_intent_key
from runtime.quantity import resolve_mint_quantity
from runtime.wallet_queue import WalletQueue


WALLET = '0x' + 'ab' * 20
OTHER = '0x' + 'cd' * 20
RECIPIENT = '0x' + '11' * 20


def raw(**overrides):
    value = dict(project_id='project', activity_id='drop', chain_id=1, wallet=WALLET, recipient=RECIPIENT)
    value.update(overrides)
    return value


class Caps:
    def __init__(self, caps):
        self.caps = caps
        self.seen = []

    def mint_caps(self, order, snapshot):
        self.seen.append(snapshot)
        return self.caps


class QuantityPolicyTests(unittest.TestCase):
    def test_omitted_quantity_is_fill_to_max(self):
        body = intent(raw())
        self.assertIsNone(body['quantity'])
        self.assertEqual(set(body), {'project_id', 'activity_id', 'chain_id', 'wallet', 'recipient', 'quantity'})
        self.assertEqual(intent(raw(quantity='max'))['quantity'], None)
        self.assertEqual(intent(raw(quantity='2'))['quantity'], '2')

    def test_require_explicit_rejects_omission(self):
        with self.assertRaisesRegex(ValueError, 'explicit quantity'):
            intent(raw(), quantity_policy='require_explicit')
        with self.assertRaisesRegex(ValueError, 'invalid quantity policy'):
            intent(raw(), quantity_policy='always')

    def test_fill_uses_tightest_wallet_and_per_tx_cap(self):
        intent_body = {'quantity': None}
        caps = {'wallet_remaining': '5', 'per_tx': '2', 'supply_remaining': '9', 'ignored': '1'}
        self.assertEqual(resolve_mint_quantity(intent_body, caps, installer_per_tx='3', mode='fill_to_max'), '2')
        self.assertEqual(resolve_mint_quantity(intent_body, {'wallet_remaining': '5', 'per_tx': '4', 'balance_limited': '1'}, mode='fill_to_max'), '1')
        self.assertEqual(resolve_mint_quantity({'quantity': '2'}, {'wallet_remaining': '5', 'per_tx': '4'}, installer_per_tx=3, mode='explicit'), '2')

    def test_explicit_quantity_cannot_exceed_caps(self):
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            resolve_mint_quantity({'quantity': '5'}, {'wallet_remaining': '5', 'per_tx': '2'}, mode='explicit')
        with self.assertRaisesRegex(ValueError, 'no remaining'):
            resolve_mint_quantity({'quantity': None}, {'wallet_remaining': '0', 'per_tx': '2'}, mode='fill_to_max')
        with self.assertRaisesRegex(ValueError, 'wallet and per-tx'):
            resolve_mint_quantity({'quantity': None}, {'wallet_remaining': '5'}, mode='fill_to_max')
        with self.assertRaisesRegex(ValueError, 'per-transaction share cap'):
            resolve_mint_quantity({'quantity': None}, {'wallet_remaining': '5', 'per_tx': '2'}, installer_per_tx=True, mode='fill_to_max')

    def test_explicit_identity_key_stays_compatible(self):
        body = intent(raw(quantity='2'))
        self.assertEqual(order_intent_key(body, 'explicit'), digest(body))
        self.assertNotEqual(order_intent_key(body, 'explicit'), order_intent_key(body, 'fill_to_max'))

    def test_defaults_file_matches_runtime_default(self):
        defaults = json.loads(Path('config/personal.defaults.json').read_text())
        self.assertEqual(defaults['quantity']['when_unspecified'], 'fill_to_max')
        self.assertIsNone(defaults['limits']['per_transaction_shares'])


class OrderAndCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = OrderStore(str(Path(self.tmp.name) / 'orders.sqlite'))
        self.queue = WalletQueue(str(Path(self.tmp.name) / 'orders.sqlite'))
        self.coord = MintCoordinator(self.store, None, None, self.queue, limits={'per_transaction_shares': '3'})

    def tearDown(self):
        self.store.close()
        self.queue.close()
        self.tmp.cleanup()

    def test_prepare_resolves_fill_without_authorizing_send(self):
        order = self.store.create('owner', 'req-1', raw())
        self.assertEqual(order['quantity_mode'], 'fill_to_max')
        self.assertIsNone(order['intent']['quantity'])
        host = Caps({'wallet_remaining': '9', 'per_tx': '4'})
        resolved = self.coord.sync_quantity('owner', order, {'snapshot': True}, host, rewrite=True)
        self.assertEqual(resolved['intent']['quantity'], '3')
        self.assertEqual(resolved['state'], 'preparing')
        self.assertIsNone(resolved['confirmation'])
        self.assertIsNone(resolved['plan'])
        self.assertNotIn(resolved['state'], {'ready', 'dispatching'})
        self.assertEqual(host.seen, [{'snapshot': True}])
        again = self.coord.sync_quantity('owner', resolved, None, host, rewrite=True)
        self.assertEqual(again['id'], resolved['id'])
        self.assertEqual(again['revision'], resolved['revision'])

    def test_fill_dedup_does_not_copy_across_wallets_or_open_a_second_spend(self):
        first = self.store.create('owner', 'req-1', raw())
        self.store.bind_quantity('owner', first['id'], '3')
        retried = self.store.create('owner', 'req-2', raw(wallet='0x' + 'AB' * 20))
        self.assertEqual(retried['id'], first['id'])
        self.assertEqual(retried['intent']['quantity'], '3')
        other = self.store.create('owner', 'req-3', raw(wallet=OTHER))
        self.assertNotEqual(other['id'], first['id'])
        self.assertIsNone(other['intent']['quantity'])
        explicit = self.store.create('owner', 'req-4', raw(quantity='1'))
        self.assertEqual(explicit['quantity_mode'], 'explicit')
        self.assertNotEqual(explicit['id'], first['id'])
        same_explicit = self.store.create('owner', 'req-5', raw(quantity='1'))
        self.assertEqual(same_explicit['id'], explicit['id'])

    def test_repeat_after_completion_requires_explicit_repeat(self):
        first = self.store.create('owner', 'req-1', raw())
        finished = self.store.get('owner', first['id'])
        finished['state'] = 'complete'
        self.store._save(finished)
        self.assertEqual(self.store.create('owner', 'req-2', raw())['id'], first['id'])
        fresh = self.store.create('owner', 'req-3', raw(), explicit_repeat=True)
        self.assertNotEqual(fresh['id'], first['id'])
        self.assertIsNone(fresh['intent']['quantity'])

    def test_cap_drift_refreshes_and_releases_without_sending(self):
        order = self.store.create('owner', 'req-1', raw())
        self.store.bind_quantity('owner', order['id'], '3')
        ready = self.store.get('owner', order['id'])
        ready.update(state='ready', confirmation='signed', plan={'marker': 1})
        self.store._save(ready)
        self.queue.enqueue(self.store.get('owner', order['id']), {})
        refreshed = self.coord._execute('owner', order['id'], Caps({'wallet_remaining': '1', 'per_tx': '9'}))
        self.assertEqual(refreshed['state'], 'needs_refresh')
        self.assertIsNone(refreshed['confirmation'])
        self.assertEqual(refreshed['intent']['quantity'], '3')
        self.assertEqual(self.queue.status(), [])

    def test_over_cap_explicit_does_not_send(self):
        order = self.store.create('owner', 'req-1', raw(quantity='5'))
        ready = self.store.get('owner', order['id'])
        ready.update(state='ready', confirmation='signed', plan={'marker': 1})
        self.store._save(ready)
        self.queue.enqueue(self.store.get('owner', order['id']), {})
        with self.assertRaisesRegex(ValueError, 'exceeds'):
            self.coord._execute('owner', order['id'], Caps({'wallet_remaining': '9', 'per_tx': '4'}))
        stored = self.store.get('owner', order['id'])
        self.assertEqual(stored['state'], 'needs_refresh')
        self.assertEqual(stored['intent']['quantity'], '5')
        self.assertEqual(self.queue.status(), [])

    def test_missing_cap_reader_is_refused(self):
        order = self.store.create('owner', 'req-1', raw(quantity='1'))
        with self.assertRaisesRegex(PermissionError, 'mint_caps'):
            self.coord.sync_quantity('owner', order, None, object(), rewrite=True)


class RuntimeDefaultTests(unittest.TestCase):
    def test_runtime_defaults_to_fill_and_can_require_explicit(self):
        class Host:
            verified_original_methods = {}

            def run_original_agent(self, envelope):
                raise AssertionError(envelope)

        with tempfile.TemporaryDirectory() as directory:
            with BotRuntime(directory, 'owner', {}, Host()) as runtime:
                order = runtime.select('mint', raw())
                self.assertEqual(order['quantity_mode'], 'fill_to_max')
                self.assertIsNone(order['intent']['quantity'])
                explicit = runtime.select('one', raw(quantity='1'))
                self.assertEqual(explicit['intent']['quantity'], '1')
        with tempfile.TemporaryDirectory() as directory:
            config = {'quantity': {'when_unspecified': 'require_explicit'}}
            with BotRuntime(directory, 'owner', config, Host()) as runtime:
                with self.assertRaisesRegex(ValueError, 'explicit quantity'):
                    runtime.select('mint', raw())
        with self.assertRaisesRegex(ValueError, 'invalid quantity policy'):
            BotRuntime('/tmp/unused-quantity-policy', 'owner', {'quantity': {'when_unspecified': 'max'}}, Host())


if __name__ == '__main__':
    unittest.main()
