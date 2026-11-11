from __future__ import annotations

import contextlib
import csv
import io
import tempfile
import unittest
from pathlib import Path

import mais_dados_cnpj as mdc

CNPJ_A = "26433520000181"
CNPJ_B = "00000000000191"


class ClassificarTest(unittest.TestCase):
    def test_cnpj_com_mascara(self) -> None:
        self.assertEqual(mdc.classificar(" 26.433.520/0001-81 "), ("cnpj", CNPJ_A))

    def test_cnpj_numerico_sem_zeros_a_esquerda(self) -> None:
        self.assertEqual(mdc.classificar("360305000104"), ("cnpj", "00360305000104"))

    def test_cnpj_alfanumerico(self) -> None:
        self.assertEqual(
            mdc.classificar("12.abc.345/01de-35"), ("cnpj", "12ABC34501DE35")
        )

    def test_cpf_e_ignorado_com_ou_sem_mascara(self) -> None:
        self.assertEqual(mdc.classificar("111.444.777-35")[0], "cpf")
        self.assertEqual(mdc.classificar("11144477735")[0], "cpf")

    def test_digito_verificador_errado(self) -> None:
        self.assertEqual(mdc.classificar("26.433.520/0001-82")[0], "cnpj_invalido")

    def test_digitos_repetidos_sao_invalidos(self) -> None:
        self.assertEqual(mdc.classificar("11.111.111/1111-11")[0], "cnpj_invalido")

    def test_texto_solto_e_invalido(self) -> None:
        self.assertEqual(mdc.classificar("NAO INFORMADO")[0], "cnpj_invalido")

    def test_vazio(self) -> None:
        self.assertEqual(mdc.classificar("  - "), ("vazio", ""))


class MontarLinhaTest(unittest.TestCase):
    def test_monta_endereco_estado_e_cep(self) -> None:
        linha = mdc.montar_linha(
            CNPJ_A,
            {
                "razao_social": "EMPRESA TESTE LTDA",
                "nome_fantasia": None,
                "descricao_tipo_de_logradouro": "RUA",
                "logradouro": "DAS FLORES",
                "numero": "100",
                "bairro": "SION",
                "municipio": "BELO HORIZONTE",
                "uf": "MG",
                "cep": 1310000,
            },
        )
        self.assertEqual(
            linha,
            mdc.Linha(
                cnpj="26.433.520/0001-81",
                razao_social="EMPRESA TESTE LTDA",
                endereco="RUA DAS FLORES, 100, SION",
                cidade="BELO HORIZONTE",
                estado="MINAS GERAIS",
                cep="01310-000",
            ),
        )

    def test_campos_ausentes_nao_deixam_virgulas_sobrando(self) -> None:
        linha = mdc.montar_linha(CNPJ_A, {"logradouro": "AV BRASIL", "uf": "EX"})
        self.assertEqual(linha.endereco, "AV BRASIL")
        self.assertEqual(linha.estado, "EX")
        self.assertEqual(linha.cep, "")


class ProcessarTest(unittest.TestCase):
    def executar(
        self, documentos: list[str], cache: dict[str, mdc.Linha] | None = None
    ) -> tuple[list[mdc.Linha], dict[str, int], list[str], list[str]]:
        consultados: list[str] = []
        salvos: list[str] = []

        def consultar(cnpj: str) -> mdc.Linha:
            consultados.append(cnpj)
            status: mdc.Status = "erro_consulta" if cnpj == CNPJ_B else "ok"
            return mdc.Linha(cnpj=mdc.formatar_cnpj(cnpj), status=status)

        with contextlib.redirect_stdout(io.StringIO()):
            linhas, contagem = mdc.processar(
                documentos,
                consultar,
                cache or {},
                lambda chave, _: salvos.append(chave),
            )
        return linhas, dict(contagem), consultados, salvos

    def test_ignora_cpf_e_vazio_e_nao_repete_cnpj(self) -> None:
        linhas, contagem, consultados, _ = self.executar(
            ["26.433.520/0001-81", "111.444.777-35", "", CNPJ_A, "26.433.520/0001-82"]
        )
        self.assertEqual(consultados, [CNPJ_A])
        self.assertEqual(
            [(linha.cnpj, linha.status) for linha in linhas],
            [("26.433.520/0001-81", "ok"), ("26.433.520/0001-82", "cnpj_invalido")],
        )
        self.assertEqual(
            contagem,
            {"cpf": 1, "vazio": 1, "repetido": 1, "ok": 1, "cnpj_invalido": 1},
        )

    def test_usa_cache_e_so_salva_resultado_final(self) -> None:
        em_cache = mdc.Linha(cnpj="26.433.520/0001-81", razao_social="DO CACHE")
        linhas, _, consultados, salvos = self.executar(
            [CNPJ_A, CNPJ_B], {CNPJ_A: em_cache}
        )
        self.assertEqual(consultados, [CNPJ_B])
        self.assertEqual(salvos, [])
        self.assertEqual(linhas[0], em_cache)
        self.assertEqual(linhas[1].status, "erro_consulta")

    def test_informa_posicao_total_e_status_de_cada_cnpj(self) -> None:
        eventos: list[tuple[int, int, str]] = []
        mdc.processar(
            [CNPJ_A, "26.433.520/0001-82"],
            lambda cnpj: mdc.Linha(cnpj=cnpj),
            {},
            lambda chave, linha: None,
            ao_progredir=lambda posicao, total, linha: eventos.append(
                (posicao, total, linha.status)
            ),
        )
        self.assertEqual(eventos, [(1, 2, "ok"), (2, 2, "cnpj_invalido")])


class ArquivosTest(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.pasta = Path(pasta.name)

    def test_le_coluna_de_csv_com_ponto_e_virgula(self) -> None:
        arquivo = self.pasta / "entrada.csv"
        arquivo.write_text(
            "EMPRESA;CPF/CNPJ\nA;26.433.520/0001-81\nB\n", encoding="utf-8"
        )
        self.assertEqual(mdc.ler_documentos(arquivo), ["26.433.520/0001-81", ""])

    def test_coluna_ausente_lista_as_disponiveis(self) -> None:
        arquivo = self.pasta / "entrada.csv"
        arquivo.write_text("EMPRESA,CEP\nA,1\n", encoding="utf-8")
        with self.assertRaisesRegex(mdc.ErroEntrada, "EMPRESA, CEP"):
            mdc.ler_documentos(arquivo)

    def test_csv_gravado_preserva_virgulas_do_endereco(self) -> None:
        destino = self.pasta / "saida.csv"
        linha = mdc.Linha(cnpj="26.433.520/0001-81", endereco="RUA A, 1, CENTRO")
        mdc.gravar_csv([linha], destino)
        with destino.open(encoding="utf-8-sig", newline="") as origem:
            registros = list(csv.DictReader(origem))
        self.assertEqual(list(registros[0]), mdc.COLUNAS_SAIDA)
        self.assertEqual(registros[0]["endereco"], "RUA A, 1, CENTRO")

    def test_cache_ignora_linha_truncada(self) -> None:
        arquivo = self.pasta / "cache.jsonl"
        arquivo.write_text(
            '{"chave": "26433520000181", "linha": {"cnpj": "x"}}\n{"chave": "0000',
            encoding="utf-8",
        )
        self.assertEqual(mdc.carregar_cache(arquivo), {CNPJ_A: mdc.Linha(cnpj="x")})


if __name__ == "__main__":
    unittest.main()
