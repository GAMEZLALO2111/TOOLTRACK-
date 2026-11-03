import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from demandas_api import _analizar, _assign_estados, _estado, _hr, _validate_marca


def _program(piezas_blank, cycle_con_lead_s):
    return SimpleNamespace(piezas_blank=piezas_blank, cycle_con_lead_s=cycle_con_lead_s)


class DemandaRulesTests(unittest.TestCase):
    def test_hr_uses_cycle_with_lead_per_piece_over_blank(self):
        # 42 pzas por blank, 726 s con lead: 1000 pzas -> 1000 * 726 / (42 * 3600)
        hr = _hr(_program(42, 726), 1000)
        self.assertAlmostEqual(hr, round(1000 * 726 / (42 * 3600), 2))

    def test_hr_none_without_program_values(self):
        self.assertIsNone(_hr(None, 100))
        self.assertIsNone(_hr(_program(None, 726), 100))
        self.assertIsNone(_hr(_program(14, None), 100))

    def test_estado_precedence(self):
        self.assertEqual(_estado(0, "X", "preparing", "X"), "TERMINADO")
        self.assertEqual(_estado(5, "NP1", "preparing", "NP1"), "CORRIENDO")
        self.assertEqual(_estado(5, None, "preparing", "NP1"), "SURTIENDO")
        self.assertEqual(_estado(5, None, "paused", "NP1"), "FALTA HERRAMIENTAS")
        self.assertEqual(_estado(5, None, "ready", "NP1"), "EN MÁQUINA")
        self.assertEqual(_estado(5, None, None, "NP1"), "NO INICIADO")

    def test_marca_requires_known_value_and_comment(self):
        _validate_marca(None, None)
        _validate_marca("URGENTE", "cliente pidió antes")
        with self.assertRaises(HTTPException):
            _validate_marca("URGENTE", "   ")
        with self.assertRaises(HTTPException):
            _validate_marca("OTRA", "algo")


class SemaforoTests(unittest.TestCase):
    @staticmethod
    def _item(id_, np, pendiente, hr_pend, marca=None):
        return {"id": id_, "numero_parte": np, "pendiente": pendiente, "hr_pendiente": hr_pend,
                "marca": marca, "estado": None}

    def _run(self, running, items, kits=None):
        _assign_estados(items, running, kits or {})
        return _analizar(running, items)

    def test_sin_cola(self):
        self.assertEqual(self._run("A", [])["nivel"], "SIN_COLA")

    def test_umbrales_por_hr_de_la_np_actual(self):
        for hr, nivel in ((1.9, "ROJO"), (2.0, "AMARILLO"), (3.9, "AMARILLO"), (4.0, "VERDE")):
            res = self._run("A", [self._item(1, "A", 10, hr)])
            self.assertEqual(res["nivel"], nivel, hr)
            self.assertEqual(res["actual_id"], 1)

    def test_surtir_urgente_cuando_pronta_a_parar_y_siguiente_sin_surtir(self):
        items = [self._item(1, "A", 10, 1.0), self._item(2, "B", 10, 5.0)]
        res = self._run("A", items)
        self.assertTrue(res["surtir_urgente"])
        self.assertEqual(res["siguiente_id"], 2)
        listo = self._run("A", [self._item(1, "A", 10, 1.0), self._item(2, "B", 10, 5.0)], {"B": "ready"})
        self.assertFalse(listo["surtir_urgente"])

    def test_no_urgente_si_hay_horas_de_sobra(self):
        res = self._run("A", [self._item(1, "A", 10, 8.0), self._item(2, "B", 10, 5.0)])
        self.assertEqual(res["nivel"], "VERDE")
        self.assertFalse(res["surtir_urgente"])

    def test_maquina_vacia_con_cola_es_roja(self):
        res = self._run(None, [self._item(1, "A", 10, 5.0)])
        self.assertEqual(res["nivel"], "ROJO")
        self.assertIsNone(res["actual_id"])

    def test_np_corriendo_terminada_pide_cambio(self):
        res = self._run("A", [self._item(1, "A", 0, 0.0), self._item(2, "B", 10, 5.0)])
        self.assertEqual(res["nivel"], "ROJO")
        self.assertIn("change over", res["motivo"])

    def test_corre_np_fuera_de_la_cola_es_amarillo(self):
        res = self._run("X", [self._item(1, "B", 10, 5.0)])
        self.assertEqual(res["nivel"], "AMARILLO")

    def test_cola_terminada_es_roja(self):
        self.assertEqual(self._run("A", [self._item(1, "A", 0, 0.0)])["nivel"], "ROJO")

    def test_paro_programado_no_alarma(self):
        res = self._run("A", [self._item(1, "A", 10, 1.0, "PARO PROGRAMADO")])
        self.assertEqual(res["nivel"], "PARO")

    def test_sin_programa_es_amarillo(self):
        self.assertEqual(self._run("A", [self._item(1, "A", 10, None)])["nivel"], "AMARILLO")

    def test_np_repetida_solo_la_primera_corre_y_toma_kit(self):
        items = [self._item(1, "A", 10, 3.0), self._item(2, "A", 10, 3.0), self._item(3, "A", 10, 3.0)]
        _assign_estados(items, "A", {"A": "preparing"})
        self.assertEqual([i["estado"] for i in items], ["CORRIENDO", "SURTIENDO", "NO INICIADO"])


if __name__ == "__main__":
    unittest.main()
