# MaisDadosCNPJ

Busca razão social, nome fantasia e endereço de cada CNPJ da planilha na BrasilAPI e salva um CSV no Drive.

## Como usar

1. Abra a [planilha de CNPJs](https://docs.google.com/spreadsheets/d/1pXtRNkYXx6UHHQulyLstIZWOcF8TNThRd7lOcNUtZ78/edit?gid=950785462#gid=950785462) e clique em **Arquivo > Fazer download > Valores separados por vírgula (.csv)**.
2. Dê dois cliques em **`Abrir MaisDadosCNPJ.bat`**.
3. Em **1. Planilha de CNPJs**, clique em **Escolher…** e selecione o `.csv` baixado.
4. Clique em **Consultar CNPJs** e aguarde (cerca de 15 minutos para 1.500 CNPJs).
5. Clique em **Abrir CSV**.

O resultado fica em [csv_consolidados](https://drive.google.com/drive/folders/1rvgrMYPysBRuDbFMJvdQ_djaXNYnuFxg?hl=pt-br) com o nome `empresas_brasilapi_<data>.csv`.

Cancelou ou fechou no meio? Abra de novo e clique em **Consultar CNPJs**: continua de onde parou.

## Requisitos

- [uv](https://docs.astral.sh/uv/) instalado.
- Google Drive para Desktop aberto (ou escolha outra pasta no passo 2 da janela).

## O que vem no CSV

Colunas: `cnpj`, `razao_social`, `nome_fantasia`, `endereco` (logradouro, número, bairro), `cidade`, `estado`, `cep`, `status`.

CPFs e CNPJs repetidos da planilha são ignorados.

| status | significado |
| --- | --- |
| `ok` | dados encontrados |
| `cnpj_invalido` | dígito verificador errado ou valor que não é CNPJ |
| `nao_encontrado` | CNPJ válido sem cadastro na BrasilAPI |
| `erro_consulta` | falha de rede ou limite da API; consulte de novo para repetir só esses |

## Pelo terminal (opcional)

```powershell
uv run python -X utf8 mais_dados_cnpj.py "<planilha.csv>"
```

Sem o caminho, usa o `.csv` mais recente da pasta `entrada/`. Opções: `--saida <pasta>`, `--coluna <nome>`, `--limite <n>`, `--intervalo <segundos>`, `--reconsultar`.

Testes: `uv run python -X utf8 -m unittest discover -s tests`
