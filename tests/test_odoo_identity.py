"""Ejecuta los métodos reales con dobles del ORM; no sustituye instalar en Odoo 16."""
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

MODULE = Path(os.environ['HKS_ODOO_MODULE']) if 'HKS_ODOO_MODULE' in os.environ else Path(__file__).resolve().parent / 'fixtures' / 'odoo-4.1.5'


class ValidationError(ValueError):
    pass


class ModelStub:
    def __getitem__(self, key):
        return getattr(self, key)

    def write(self, vals):
        for name, value in vals.items():
            setattr(self, name, value)
        return True

    def __iter__(self):
        return iter([self])

    def ensure_one(self):
        pass

    def _get_pos_ui_pos_config(self, params):
        return {'id': self.config_id.id, 'existing': 'preserved'}


def load_model():
    api = SimpleNamespace(depends=lambda *_: lambda func: func, constrains=lambda *_: lambda func: func)
    fields = SimpleNamespace(Char=lambda **_: None, Selection=lambda *_, **kw: None, Text=lambda **_: None, Boolean=lambda **_: None)
    odoo = SimpleNamespace(api=api, fields=fields, models=SimpleNamespace(Model=ModelStub), _=lambda text: text)
    spec = importlib.util.spec_from_file_location('hks_identity_under_test', MODULE / 'models/pos_fiscal_identity.py')
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {'odoo': odoo, 'odoo.exceptions': SimpleNamespace(ValidationError=ValidationError)}):
        spec.loader.exec_module(module)
    return module


class OdooIdentityTests(unittest.TestCase):
    def setUp(self):
        self.module = load_model()
        self.config = self.module.PosConfig()
        self.config.env = SimpleNamespace(cr=SimpleNamespace(dbname='base_compartida'), company=SimpleNamespace(id=999))
        self.config.id = 24
        self.config.hks_ff_mode = 'local'
        self.config.has_active_session = False
        self.config.company_id = SimpleNamespace(id=18)
        self.config.hks_ff_emisor = 'RUC-EMISOR'
        self.config.hks_ff_ambiente = '2'
        self.config.hks_ff_sucursal = '0000'
        self.config.hks_ff_punto = '001'

    def test_loader_uses_session_config_company_not_active_environment_company(self):
        session = self.module.PosSession()
        session.config_id = self.config
        session.env = self.config.env
        loaded = session._get_pos_ui_pos_config({})
        self.assertEqual(loaded['existing'], 'preserved')
        self.assertEqual(loaded['hks_ff_identity']['odoo_company_id'], '18')
        self.assertEqual(loaded['hks_ff_identity']['odoo_pos_config_id'], '24')
        self.assertEqual(loaded['hks_ff_identity']['odoo_database'], 'base_compartida')

    def test_configuration_incomplete_blocks_pos_load(self):
        for name in ('hks_ff_emisor', 'hks_ff_ambiente', 'hks_ff_sucursal', 'hks_ff_punto'):
            with self.subTest(name=name), patch.object(self.config, name, False):
                with self.assertRaisesRegex(ValidationError, 'Complete el perfil'):
                    self.config._hks_ff_identity()

    def test_invalid_profile_is_not_exported(self):
        for name, value in (('hks_ff_emisor', 'A\nB'), ('hks_ff_emisor', ' '),
                            ('hks_ff_sucursal', '00001'), ('hks_ff_sucursal', '4b00'),
                            ('hks_ff_sucursal', '4-00'), ('hks_ff_sucursal', '４B00'),
                            ('hks_ff_punto', '000'), ('hks_ff_punto', '１23')):
            with self.subTest(name=name, value=value), patch.object(self.config, name, value):
                with self.assertRaises(ValidationError):
                    self.config._hks_ff_identity()

    def test_alphanumeric_branch_is_exported_without_substitution(self):
        self.config.hks_ff_sucursal = '4B00'
        identity = self.config._hks_ff_identity()
        self.assertEqual(identity['sucursal'], '4B00')
        self.config._compute_hks_ff_config_info()
        self.assertIn('sucursal = 4B00', self.config.hks_ff_config_info)

    def test_displayed_ini_matches_exported_identity(self):
        import configparser
        from fiscal_sequence import scope_from_config
        self.config._compute_hks_ff_config_info()
        config = configparser.ConfigParser(interpolation=None)
        config.read_string(self.config.hks_ff_config_info)
        self.assertEqual(scope_from_config(config), self.config._hks_ff_identity())

    def test_unique_scope_does_not_depend_on_odoo_company_name_or_id(self):
        self.config._compute_hks_ff_scope_key()
        original = self.config.hks_ff_scope_key
        self.config.company_id = SimpleNamespace(id=19)
        self.config.hks_ff_emisor = ' ruc-emisor '
        self.config._compute_hks_ff_scope_key()
        self.assertEqual(self.config.hks_ff_scope_key, original)
        self.config.hks_ff_punto = '002'
        self.config._compute_hks_ff_scope_key()
        self.assertNotEqual(self.config.hks_ff_scope_key, original)
        self.config.hks_ff_emisor = False
        self.config._compute_hks_ff_scope_key()
        self.assertFalse(self.config.hks_ff_scope_key)

    def test_legacy_loader_needs_no_new_identity_fields(self):
        self.config.hks_ff_mode = 'odoo'
        self.config.hks_ff_emisor = False
        session = self.module.PosSession()
        session.config_id = self.config
        result = session._get_pos_ui_pos_config({})
        self.assertEqual(result['hks_ff_mode'], 'odoo')
        self.assertFalse(result['hks_ff_identity'])
        self.assertEqual(result['existing'], 'preserved')

    def test_legacy_config_info_does_not_instruct_local_initialization(self):
        self.config.hks_ff_mode = 'odoo'
        self.config._compute_hks_ff_config_info()
        self.assertNotIn('modo = local', self.config.hks_ff_config_info)
        self.assertIn('5.1.9', self.config.hks_ff_config_info)

    def test_open_session_blocks_mode_and_identity_change_on_server(self):
        self.config.hks_ff_mode = 'odoo'
        self.config.has_active_session = True
        for vals in ({'hks_ff_mode': 'local'}, {'hks_ff_emisor': 'NUEVO'}, {'company_id': 19}):
            with self.assertRaisesRegex(ValidationError, 'Cierre la sesión'):
                self.config.write(vals)
        self.assertEqual(self.config.hks_ff_mode, 'odoo')

    def test_local_identity_and_mode_are_not_downgraded(self):
        for vals in ({'hks_ff_mode':'odoo'}, {'hks_ff_punto':'002'}, {'company_id':19}):
            with self.assertRaisesRegex(ValidationError, 'ya usa contador local'):
                self.config.write(vals)

    def test_unchanged_profile_and_nonfiscal_edits_remain_allowed(self):
        self.config.has_active_session = True
        self.assertTrue(self.config.write({'hks_ff_mode':'local', 'hks_ff_emisor':'RUC-EMISOR'}))
        self.assertTrue(self.config.write({'name':'Nombre actualizado'}))

    def test_closed_legacy_session_can_be_activated(self):
        self.config.hks_ff_mode = 'odoo'
        self.assertTrue(self.config.write({'hks_ff_mode':'local'}))
        self.config._check_hks_ff_local_ready()

    def test_local_constraint_requires_complete_profile_but_legacy_does_not(self):
        self.config.hks_ff_emisor = False
        with self.assertRaises(ValidationError):
            self.config._check_hks_ff_local_ready()
        self.config.hks_ff_mode = 'odoo'
        self.config._check_hks_ff_local_ready()

    def test_upgrade_from_41_preserves_local_and_40_keeps_legacy_default(self):
        from unittest.mock import Mock
        spec = importlib.util.spec_from_file_location('migration_test', MODULE / 'migrations/16.0.4.1.2/post-migration.py')
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        for version in ('16.0.4.1.0', '16.0.4.1.1', '4.1.1'):
            cursor = Mock()
            migration.migrate(cursor, version)
            cursor.execute.assert_called_once_with("UPDATE pos_config SET hks_ff_mode = 'local'")
        for version in (None, '16.0.4.0', '16.0.4.1.2'):
            cursor = Mock()
            migration.migrate(cursor, version)
            cursor.execute.assert_not_called()


if __name__ == '__main__':
    unittest.main()
