# Site — Inteligência de Risco em Comodato de Chopp (PI6)

Apresentação do projeto e simulador de risco.

| sem login | com login |
|:---|:---|
| problema, evolução PI5 → PI6, arquitetura, variáveis, grade de cenários, métricas do modelo, LGPD | painel da carteira (clientes, faturamento, percentuais, carteira escorada, matriz de confusão, gráficos do EDA), simulador e trilha de auditoria (admin) |

Número da empresa não é escrito no HTML para visitante anônimo: o `app.py` só monta
a variável `painel` com usuário autenticado. Os gráficos do EDA ficam em
`restrito/graficos/`, fora de `static/`, e só são servidos por `/graficos/<arquivo>`
com login. **Não coloque gráfico com dado da empresa em `static/`**: tudo ali é
público por URL.

## Estrutura

| arquivo | papel |
|:---|:---|
| `app.py` | rotas, estado ativo (modelo + carteira), API do simulador, `/admin/fonte` |
| `fontes.py` | os dois caminhos de dados: Local e Databricks |
| `compat.py` | contorna o pyarrow bloqueado pelo Windows (causa das métricas zeradas) |
| `auth.py` | login (Flask-Login + SQLite), bloqueio por tentativas, trilha de auditoria |
| `gerenciar_usuarios.py` | criação e manutenção de usuários (não há cadastro pelo site) |
| `preparar_dados.py` | gera `data/carteira_site_v1_0.csv`; a regra (`montar_carteira`) também é usada pela fonte Databricks |
| `databricks.exemplo.json` | modelo de configuração do Databricks (sem segredos) |
| `modelo/modelo_campeao_v1_0.pkl` | pacote do notebook 08: pipeline + model card |
| `data/carteira_site_v1_0.csv` | gerado pelo `preparar_dados.py` — necessário para o simulador |
| `restrito/graficos/` | figuras do notebook 03 (dados da empresa), servidas só com login |
| `instance/` | `usuarios.db`, `secret_key`, `fonte.json` (fonte escolhida) e `databricks.json` (credenciais) — não versionar |

## Primeira execução

```bash
# instalação mínima: só wheels prontas, sem cache do pip e sem .pyc
pip install --no-cache-dir --no-compile -r requirements.txt

# 1. Base do site (só as 13 features + ID; nenhum nome)
python preparar_dados.py

# 2. Primeiro usuário (admin vê a trilha de auditoria)
python gerenciar_usuarios.py criar admin --admin

# 3. Servidor local
python app.py        # http://localhost:5000
```

Ao trocar de `DATA_VERSION`: copie o novo `modelo_campeao_v<X>.pkl` para `modelo/`,
rode `preparar_dados.py --consolidado <csv> --modelo <pkl>` e ajuste `MODEL_PATH` e
`DATASET_PATH`.

## Fonte de dados: Local ou Databricks

O administrador escolhe em **menu do usuário → Fonte de dados** (`/admin/fonte`).

| | Local | Databricks |
|:---|:---|:---|
| modelo | `modelo/modelo_campeao_v1_0.pkl` | `<VOLUME>/modelo_campeao_v1_0.pkl` (Files API) |
| carteira | `data/carteira_site_v1_0.csv` | tabela Delta do notebook 02 via SQL warehouse → `montar_carteira()` |
| gráficos | `restrito/graficos/` | `<VOLUME>/graficos/` (Files API, em cache) |

- **Testar** carrega a fonte sem ativá-la. **Ativar** só troca se a carga der certo;
  se falhar, a fonte atual continua no ar.
- A escolha fica em `instance/fonte.json` e vale para a próxima inicialização. Se o
  Databricks estiver fora do ar ao subir o site, ele usa a fonte local e avisa.
- Credenciais: variáveis `DATABRICKS_HOST`, `DATABRICKS_TOKEN`,
  `DATABRICKS_WAREHOUSE_ID` (opcionais: `DATABRICKS_TABELA`, `DATABRICKS_VOLUME`,
  `DATABRICKS_ARQUIVO_MODELO`, `DATABRICKS_PASTA_GRAFICOS`) ou
  `instance/databricks.json` (copie `databricks.exemplo.json`). O token não é
  digitado no site.
- No Databricks: tabela publicada pelo notebook 02, `.pkl` gravado pelo notebook 08
  no Volume, figuras do notebook 03 salvas em `<VOLUME>/graficos/` com os mesmos
  nomes de `restrito/graficos/`, e um SQL warehouse ligado.
- O `.pkl` precisa da mesma versão de scikit-learn do site. Se o gerado no
  Databricks usar outra, o teste avisa; alinhe o `requirements.txt`.

## Usuários

```bash
python gerenciar_usuarios.py listar
python gerenciar_usuarios.py criar maria            # perfil analista
python gerenciar_usuarios.py senha maria
python gerenciar_usuarios.py desbloquear maria      # após 5 tentativas erradas
python gerenciar_usuarios.py desativar maria
```

## Variáveis de ambiente

| variável | padrão | uso |
|:---|:---|:---|
| `SECRET_KEY` | gerada em `instance/secret_key` | assinatura da sessão |
| `COOKIE_SEGURO` | `0` | use `1` em produção com HTTPS |
| `LOGIN_SITE_INTEIRO` | `0` | `1` exige login também na apresentação |
| `SESSAO_MINUTOS` | `15` | expiração da sessão |
| `LOGIN_MAX_TENTATIVAS` / `LOGIN_MINUTOS_BLOQUEIO` | `5` / `15` | bloqueio de conta |
| `LOG_RETENCAO_DIAS` | `180` | eventos mais antigos são apagados na inicialização |
| `MODEL_PATH`, `DATASET_PATH`, `AUTH_DB_PATH` | pastas do site | caminhos da fonte local e do banco de usuários |
| `FONTE_DADOS` | `local` | fonte inicial, se `instance/fonte.json` não existir |

## Deploy (PythonAnywhere)

- Python **3.11, 3.12 ou 3.13** (o numpy 2.3 exige 3.11+). No PythonAnywhere, a
  imagem *haggis* oferece até 3.13 e a *innit* até 3.12; crie o virtualenv com uma delas.
- O `.pkl` só carrega com a mesma versão de scikit-learn que o gerou — mantenha o
  pin do `requirements.txt`.
- No WSGI, importe `application` de `app.py`; defina `SECRET_KEY` e `COOKIE_SEGURO=1`.
- `instance/usuarios.db` precisa de permissão de escrita.
