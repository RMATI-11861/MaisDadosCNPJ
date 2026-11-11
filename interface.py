"""Janela para consultar os CNPJs de uma planilha sem usar o terminal."""

from __future__ import annotations

import os
import queue
import threading
import tkinter as tk
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import mais_dados_cnpj as mdc

INTERVALO_FILA_MS = 100
FONTE = "Segoe UI"
CARTOES: tuple[tuple[mdc.Status, str, str], ...] = (
    ("ok", "Encontrados", "#1a7f37"),
    ("nao_encontrado", "Não encontrados", "#9a6700"),
    ("cnpj_invalido", "CNPJs inválidos", "#cf222e"),
    ("erro_consulta", "Erros de consulta", "#57606a"),
)


class Cancelado(Exception):
    """O usuário pediu para interromper a consulta."""


def _milhar(numero: int) -> str:
    return f"{numero:,}".replace(",", ".")


def _entrada_sugerida() -> str:
    try:
        return str(mdc.entrada_mais_recente())
    except mdc.ErroEntrada:
        return ""


class Janela:
    def __init__(self, raiz: tk.Tk) -> None:
        self.raiz = raiz
        self._fila: queue.Queue[Callable[[], None]] = queue.Queue()
        self._cancelar = threading.Event()
        self._contagem: Counter[str] = Counter()
        self._destino: Path | None = None
        self._bloqueaveis: list[ttk.Widget] = []

        self.entrada = tk.StringVar(value=_entrada_sugerida())
        self.saida = tk.StringVar(value=str(mdc.PASTA_SAIDA_PADRAO))
        self.reconsultar = tk.BooleanVar()
        self.situacao = tk.StringVar(value="Pronto para começar.")
        self.numeros = {status: tk.StringVar(value="0") for status, _, _ in CARTOES}

        self._montar()
        raiz.protocol("WM_DELETE_WINDOW", self._fechar)
        raiz.after(INTERVALO_FILA_MS, self._drenar)

    def _montar(self) -> None:
        self.raiz.title("Mais Dados CNPJ")
        self.raiz.resizable(False, False)
        estilo = ttk.Style()
        estilo.configure(".", font=(FONTE, 10))
        estilo.configure("Titulo.TLabel", font=(FONTE, 17, "bold"))
        estilo.configure("Passo.TLabel", font=(FONTE, 10, "bold"))
        estilo.configure("Dica.TLabel", foreground="#57606a", font=(FONTE, 9))
        estilo.configure("Principal.TButton", font=(FONTE, 11, "bold"), padding=(18, 8))

        corpo = ttk.Frame(self.raiz, padding=24)
        corpo.grid()
        corpo.columnconfigure(0, weight=1)

        ttk.Label(corpo, text="Mais Dados CNPJ", style="Titulo.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(
            corpo,
            text="Busca razão social, nome fantasia e endereço de cada CNPJ "
            "na BrasilAPI.",
            style="Dica.TLabel",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 18))

        self._campo(
            corpo,
            linha=2,
            titulo="1. Planilha de CNPJs (.csv)",
            variavel=self.entrada,
            ao_escolher=self._escolher_entrada,
            dica="No Google Sheets: Arquivo > Fazer download > "
            "Valores separados por vírgula (.csv)",
        )
        self._campo(
            corpo,
            linha=5,
            titulo="2. Salvar o resultado em",
            variavel=self.saida,
            ao_escolher=self._escolher_saida,
            dica="Padrão: pasta csv_consolidados do Google Drive",
        )

        opcao = ttk.Checkbutton(
            corpo,
            text="Consultar tudo de novo (ignorar o que já foi consultado antes)",
            variable=self.reconsultar,
        )
        opcao.grid(row=8, column=0, columnspan=2, sticky="w", pady=(0, 16))
        self._bloqueaveis.append(opcao)

        acoes = ttk.Frame(corpo)
        acoes.grid(row=9, column=0, columnspan=2, sticky="w")
        self.botao_iniciar = ttk.Button(
            acoes,
            text="Consultar CNPJs",
            style="Principal.TButton",
            command=self.iniciar,
        )
        self.botao_iniciar.grid(row=0, column=0)
        self.botao_cancelar = ttk.Button(
            acoes, text="Cancelar", command=self.cancelar, state="disabled"
        )
        self.botao_cancelar.grid(row=0, column=1, padx=(10, 0))

        self.barra = ttk.Progressbar(corpo, mode="determinate")
        self.barra.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(18, 6))
        ttk.Label(
            corpo, textvariable=self.situacao, wraplength=600, justify="left"
        ).grid(row=11, column=0, columnspan=2, sticky="w")

        cartoes = ttk.Frame(corpo)
        cartoes.grid(row=12, column=0, columnspan=2, sticky="ew", pady=(16, 16))
        for coluna, (status, rotulo, cor) in enumerate(CARTOES):
            cartoes.columnconfigure(coluna, weight=1, uniform="cartao")
            cartao = tk.Frame(
                cartoes,
                background="white",
                highlightthickness=1,
                highlightbackground="#d0d7de",
                padx=12,
                pady=10,
            )
            cartao.grid(
                row=0, column=coluna, sticky="ew", padx=(0 if coluna == 0 else 8, 0)
            )
            tk.Label(
                cartao,
                textvariable=self.numeros[status],
                font=(FONTE, 20, "bold"),
                foreground=cor,
                background="white",
            ).pack(anchor="w")
            tk.Label(
                cartao,
                text=rotulo,
                font=(FONTE, 9),
                foreground="#57606a",
                background="white",
            ).pack(anchor="w")

        resultado = ttk.Frame(corpo)
        resultado.grid(row=13, column=0, columnspan=2, sticky="w")
        self.botao_csv = ttk.Button(
            resultado, text="Abrir CSV", command=self._abrir_csv, state="disabled"
        )
        self.botao_csv.grid(row=0, column=0)
        self.botao_pasta = ttk.Button(
            resultado, text="Abrir pasta", command=self._abrir_pasta, state="disabled"
        )
        self.botao_pasta.grid(row=0, column=1, padx=(10, 0))

    def _campo(
        self,
        corpo: ttk.Frame,
        *,
        linha: int,
        titulo: str,
        variavel: tk.StringVar,
        ao_escolher: Callable[[], None],
        dica: str,
    ) -> None:
        ttk.Label(corpo, text=titulo, style="Passo.TLabel").grid(
            row=linha, column=0, columnspan=2, sticky="w"
        )
        caixa = ttk.Entry(corpo, textvariable=variavel, width=70)
        caixa.grid(row=linha + 1, column=0, sticky="ew", pady=4)
        botao = ttk.Button(corpo, text="Escolher…", command=ao_escolher)
        botao.grid(row=linha + 1, column=1, padx=(8, 0))
        ttk.Label(corpo, text=dica, style="Dica.TLabel").grid(
            row=linha + 2, column=0, columnspan=2, sticky="w", pady=(0, 14)
        )
        self._bloqueaveis += [caixa, botao]

    def _escolher_entrada(self) -> None:
        downloads = Path.home() / "Downloads"
        escolhido = filedialog.askopenfilename(
            title="Escolha a planilha exportada em CSV",
            initialdir=downloads if downloads.is_dir() else mdc.PASTA_ENTRADA,
            filetypes=[("Planilha CSV", "*.csv")],
        )
        if escolhido:
            self.entrada.set(str(Path(escolhido)))

    def _escolher_saida(self) -> None:
        escolhida = filedialog.askdirectory(title="Escolha onde salvar o resultado")
        if escolhida:
            self.saida.set(str(Path(escolhida)))

    def iniciar(self) -> None:
        entrada = self.entrada.get().strip()
        saida = self.saida.get().strip()
        if not entrada or not saida:
            messagebox.showwarning(
                "Falta informação",
                "Escolha a planilha .csv e a pasta onde o resultado será salvo.",
            )
            return
        self._cancelar.clear()
        self._contagem.clear()
        for numero in self.numeros.values():
            numero.set("0")
        self.barra.configure(value=0)
        self.situacao.set("Lendo a planilha…")
        self._ocupar(True)
        threading.Thread(
            target=self._trabalhar,
            args=(Path(entrada), Path(saida), self.reconsultar.get()),
            daemon=True,
        ).start()

    def cancelar(self) -> None:
        self._cancelar.set()
        self.botao_cancelar.configure(state="disabled")
        self.situacao.set("Cancelando…")

    def _trabalhar(self, entrada: Path, saida: Path, reconsultar: bool) -> None:
        def ao_progredir(posicao: int, total: int, linha: mdc.Linha) -> None:
            self._fila.put(lambda: self._progredir(posicao, total, linha))
            if self._cancelar.is_set():
                raise Cancelado

        try:
            resumo = mdc.executar(
                entrada, saida, reconsultar=reconsultar, ao_progredir=ao_progredir
            )
        except Cancelado:
            self._fila.put(self._cancelado)
        except mdc.ErroEntrada as erro:
            mensagem = str(erro)
            self._fila.put(lambda: self._falhar(mensagem))
        # Sem capturar tudo, um erro na thread deixaria a janela presa em "consultando".
        except Exception as erro:  # noqa: BLE001
            mensagem = f"Erro inesperado: {erro!r}"
            self._fila.put(lambda: self._falhar(mensagem))
        else:
            self._fila.put(lambda: self._concluir(resumo))

    def _drenar(self) -> None:
        while True:
            try:
                atualizar = self._fila.get_nowait()
            except queue.Empty:
                break
            atualizar()
        self.raiz.after(INTERVALO_FILA_MS, self._drenar)

    def _progredir(self, posicao: int, total: int, linha: mdc.Linha) -> None:
        self._contagem[linha.status] += 1
        self.numeros[linha.status].set(_milhar(self._contagem[linha.status]))
        self.barra.configure(maximum=total, value=posicao)
        if not self._cancelar.is_set():
            self.situacao.set(
                f"{_milhar(posicao)} de {_milhar(total)} CNPJs · {linha.cnpj}"
            )

    def _concluir(self, resumo: mdc.Resumo) -> None:
        self._destino = resumo.destino
        self._ocupar(False)
        for botao in (self.botao_csv, self.botao_pasta):
            botao.configure(state="normal")
        contagem = resumo.contagem
        texto = (
            f"Concluído. Arquivo salvo: {resumo.destino.name}\n"
            f"CPFs ignorados: {_milhar(contagem['cpf'])} · "
            f"CNPJs repetidos: {_milhar(contagem['repetido'])}"
        )
        if contagem["erro_consulta"]:
            texto += (
                f"\n{_milhar(contagem['erro_consulta'])} consultas falharam: clique "
                "em Consultar CNPJs de novo para repetir só essas."
            )
        self.situacao.set(texto)
        messagebox.showinfo("Consulta concluída", texto)

    def _falhar(self, mensagem: str) -> None:
        self._ocupar(False)
        self.situacao.set("Não foi possível concluir.")
        messagebox.showerror("Não foi possível concluir", mensagem)

    def _cancelado(self) -> None:
        self._ocupar(False)
        self.situacao.set(
            "Cancelado. O que já foi consultado está salvo: clique em "
            "Consultar CNPJs para continuar de onde parou."
        )

    def _ocupar(self, ocupado: bool) -> None:
        for controle in (*self._bloqueaveis, self.botao_iniciar):
            controle.state(["disabled" if ocupado else "!disabled"])
        self.botao_cancelar.configure(state="normal" if ocupado else "disabled")
        if ocupado:
            for botao in (self.botao_csv, self.botao_pasta):
                botao.configure(state="disabled")

    def _abrir_csv(self) -> None:
        if self._destino is not None:
            os.startfile(self._destino)

    def _abrir_pasta(self) -> None:
        if self._destino is not None:
            os.startfile(self._destino.parent)

    def _fechar(self) -> None:
        self._cancelar.set()
        self.raiz.destroy()


def main() -> None:
    raiz = tk.Tk()
    Janela(raiz)
    raiz.mainloop()


if __name__ == "__main__":
    main()
