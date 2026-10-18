import unittest

from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from models.inventory import (
    Base,
    ConfiguracionTN,
    Herramienta,
    HerramientaCatalogo,
    NumeroHerramienta,
    VarianteHerramienta,
)


class CatalogModelTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")

        @event.listens_for(self.engine, "connect")
        def enable_foreign_keys(connection, _record):
            cursor = connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def create_base_catalog(self):
        self.session.add(NumeroHerramienta(tn="TN-100"))
        self.session.commit()
        self.session.add(ConfiguracionTN(tn="TN-100", size="A", type="ROUND"))
        self.session.commit()

    def test_catalog_supports_clearance_variants_and_many_physical_tools(self):
        self.create_base_catalog()
        punch = VarianteHerramienta(
            tn="TN-100",
            size="A",
            categoria="punzon",
        )
        die_a = VarianteHerramienta(
            tn="TN-100",
            size="A",
            categoria="matriz",
            clearance="0.010",
        )
        die_b = VarianteHerramienta(
            tn="TN-100",
            size="A",
            categoria="matriz",
            clearance="0.020",
        )
        self.session.add_all([punch, die_a, die_b])
        self.session.flush()
        tool_a = Herramienta(
            id="PHYS-1",
            tn="TN-100",
            size="A",
            type="ROUND",
            categoria="punzon",
        )
        tool_b = Herramienta(
            id="PHYS-2",
            tn="TN-100",
            size="A",
            type="ROUND",
            categoria="punzon",
        )
        tool_a.catalogo = HerramientaCatalogo(variante=punch)
        tool_b.catalogo = HerramientaCatalogo(variante=punch)
        self.session.add_all([tool_a, tool_b])
        self.session.commit()

        self.assertEqual(len(punch.herramientas_fisicas), 2)
        self.assertEqual(
            {variant.clearance for variant in self.session.query(VarianteHerramienta).filter_by(categoria="matriz")},
            {"0.010", "0.020"},
        )

    def test_configuration_requires_matching_tool_number(self):
        self.session.add(ConfiguracionTN(tn="MISSING", size="A", type="ROUND"))

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_only_one_punch_variant_per_tn_and_size(self):
        self.create_base_catalog()
        self.session.add_all([
            VarianteHerramienta(tn="TN-100", size="A", categoria="punzon"),
            VarianteHerramienta(tn="TN-100", size="A", categoria="punzon"),
        ])

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_die_variant_requires_clearance(self):
        self.create_base_catalog()
        self.session.add(
            VarianteHerramienta(tn="TN-100", size="A", categoria="matriz")
        )

        with self.assertRaises(IntegrityError):
            self.session.commit()

    def test_deleting_legacy_physical_tool_cleans_catalog_link(self):
        self.create_base_catalog()
        variant = VarianteHerramienta(tn="TN-100", size="A", categoria="punzon")
        tool = Herramienta(id="PHYS-1", tn="TN-100", size="A", type="ROUND")
        tool.catalogo = HerramientaCatalogo(variante=variant)
        self.session.add(tool)
        self.session.commit()

        self.session.delete(tool)
        self.session.commit()

        self.assertEqual(self.session.query(HerramientaCatalogo).count(), 0)


if __name__ == "__main__":
    unittest.main()
