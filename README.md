# Modelador ER 365 — Chen/EER (Navathe 6ª Ed.) & Notação de Barker

Aplicativo desktop profissional em Python/Tkinter para modelagem conceitual e lógica de banco de dados, com interface moderna inspirada no **Microsoft Office 365 / Excel** e **Lucidchart**. Suporta os padrões formais da **Notação de Chen** (*Sistemas de Banco de Dados*, Ramez Elmasri & Shamkant B. Navathe, 6ª Edição, Capítulo 3) e **Diagramação por Tabelas Relacionais**, com mapeamento ER-para-Relacional completo (Capítulo 9), geração de DDL (PostgreSQL, MySQL, Oracle) e exportação em HTML e PDF.

---


---



## 🆕 Versão 3 — Interface e Oracle

- **Menu suspenso** (Arquivo · Editar · Exibir · Inserir · Banco de dados · Ajuda): Novo, Abrir, Criar a partir de exemplo,
  Salvar, Salvar como, **Salvar todos**, Exportar e Gravar DDL. Os botões de arquivo saíram das abas.
- **Barra de navegação fixa** abaixo da faixa de opções: desfazer/refazer, zoom −/+ com caixa de porcentagem, 100%,
  Ajustar, Zoom em área, Centralizar seleção, notação Chen/Barker, Grade/Encaixar/Legenda e **busca** de entidade,
  atributo ou relacionamento (Ctrl+F, Enter percorre os resultados). Botões com dicas ao passar o mouse.
- **Entidade forte/fraca** só na paleta lateral (e no menu Inserir). A paleta tem grupos recolhíveis e a faixa de opções
  pode ser recolhida (▲ ou Ctrl+F1) para ganhar espaço.
- **Oracle — conexão persistente**: fechar a janela não desconecta; a conexão vive enquanto o app estiver aberto, aparece
  na barra de status (🟢/⚪, clique para abrir) e é revalidada antes de cada operação. A senha nunca é guardada.
- **Oracle — dicionário de dados**: nova aba que navega por schema/tabela com busca e, para cada tabela, mostra resumo
  (linhas, última análise, tablespace), colunas (tipo, nulo, PK/FK, comentário), constraints, índices, privilégios,
  FKs que a referenciam, DDL real (DBMS_METADATA) e amostra de dados somente leitura. A verificação do modelo ganhou
  cartões de resumo, filtros, busca e exportação CSV.

---

## 🆕 Versão 2 — Barker ortogonal, Oracle e gravação de DDL

- **Linhas do Barker no padrão moderno**: conectores ortogonais que saem perpendiculares à borda da entidade, contornam
  as demais tabelas (roteamento com A* em grade de visibilidade), com cantos arredondados, portas distintas quando várias
  linhas chegam ao mesmo lado e corredores diferentes para linhas paralelas. Durante o arrasto de entidades a rota é
  simplificada para manter a fluidez; ao soltar, a rota completa é recalculada.
- **Oracle — dicionário de dados** (aba *DDL & Banco de Dados* → *Verificar e criar no Oracle…*): lê `ALL_*` (ou `DBA_*`
  quando o usuário tem acesso) — tabelas, colunas, tipos, PK/FK, comentários, privilégios, sinônimos — e compara com o
  modelo: *nova*, *já existe no destino*, *existe em outro schema*, com diferença coluna a coluna.
- **Criação no schema de destino**: gera um plano revisável (você marca/desmarca cada comando). Tabelas novas são criadas
  no schema escolhido; tabelas que já existem em outro schema recebem `GRANT` (SELECT, INSERT, UPDATE, DELETE,
  REFERENCES) para o novo schema + `CREATE SYNONYM`; opcionalmente cria o schema (`CREATE USER`) e adiciona colunas que
  faltam. **Nunca apaga nem sobrescreve nada**; a execução exige confirmação e grava um log. A senha não é salva.
- **Gravar DDL**: botões *Transacional…*, *Dimensional…* e *Ambos…* gravam `.sql` no dialeto escolhido (Oracle inclui
  `SET DEFINE OFF`).
- Dependência nova (só para o Oracle): `pip install oracledb` (modo thin, não precisa do Oracle Client).

---

## 🆕 Novidades desta versão

- **Descrição em tudo**: entidades, atributos, sub-atributos, relacionamentos e domínios têm descrição. No DDL ela vira
  `COMMENT ON TABLE/COLUMN` (PostgreSQL, Oracle) ou `COMMENT '...'` (MySQL), inclusive nas tabelas associativas,
  multivaloradas e dimensionais (Kimball).
- **Todos os elementos de Chen/EER** (aba *Inserir Elementos*): entidade forte/fraca, relacionamento binário,
  identificador (losango duplo), **n-ário (3+ entidades)**, **especialização, generalização e categoria (união)** com
  círculo `d`/`o`/`u`, linha simples/dupla (parcial/total), atributo definidor e símbolo ⊂. Cardinalidade em razão
  (1:N) ou **(mín,máx)**.
- **Notação de Barker** (substitui o antigo "Diagrama de Tabelas"): caixas arredondadas, `#` identificador único,
  `*` obrigatório, `o` opcional, linha cheia/tracejada (obrigatório/opcional), pé-de-galinha, barra de UID para
  relacionamentos identificadores, **subtipos aninhados** no supertipo e relacionamento n-ário como entidade de
  interseção. Projetos antigos com `"notation": "table"` abrem em Barker.
- **Domínios definidos pelo usuário** (`Domínios…`): ex. *Status: 1 = Atualizado, 2 = Desatualizado, 3 = Fechado*.
  Aceita colar a lista (`1 - Atualizado`, `2 = Desatualizado`, colunas do Excel). O atributo escolhe o domínio e o DDL
  gera **tabela de domínio + INSERTs + FK** ou apenas **CHECK ... IN (...)**, conforme a opção do domínio.
- **Mapeamento EER no DDL**: uma tabela por classe (8A) ou tabela única com discriminador/flags (8C/8D); categorias com
  chave substituta; relacionamentos n-ários (passo 7); atributo *Único* (UNIQUE).
- **Validação do modelo** (F5): entidade sem chave, fraca sem identificador, nomes duplicados, domínios inconsistentes etc.
  O DDL avisa antes de gerar se houver erros.

## ⌨ Usabilidade

Duplo-clique no fundo cria entidade · botão direito abre menu de contexto (ou arrasta a visão) · Ctrl+Z/Y, Ctrl+D,
Del, F2/Enter, setas movem a seleção · *Encaixar na grade* · mover só após 4 px de arrasto · legenda da notação ativa ·
modos Relacionar/N-ário com aviso na tela e Esc para cancelar · edição de entidade/relacionamento trabalha numa cópia
(Fechar/Esc descarta) · F1 mostra todos os atalhos · o salvamento automático grava apenas um **rascunho de
recuperação**; o arquivo do projeto só muda com Ctrl+S.

---

## 🖥 Interface Estilo Office 365 & Lucidchart

1. **Barra Superior & Faixa de Opções (Ribbon Office 365)**:
   - Barra temática verde Excel com identificação do modelo e status de salvamento automático (`🟢 Salvo localmente`).
   - Abas da Faixa de Opções:
     - **Página Inicial**: botões de modelagem (`➕ Entidade`, `🔗 Relacionar`, `🗑 Excluir`), seletor segmentado de notação (`📐 Chen / EER` e `📊 Barker`), carregamento do exemplo Navathe e operações de arquivo.
     - **Exibir & Layout**: controle de navegação e zoom (`🔍+`, `🔍−`, `100%`, `⛶ Ajustar Tudo`), **slider de raio/espaçamento dos atributos** (55px a 170px) com presets (*Compacto*, *Normal*, *Amplo*), alternância de grade pontilhada e paleta lateral.
     - **DDL & Banco de Dados**: seleção de SGBD (`PostgreSQL`, `MySQL`, `Oracle`) e geração do script SQL de acordo com os 6 passos formais de Navathe.
     - **Exportar Relatórios**: exportação em HTML clean com layout de cards e exportação em PDF técnico.
2. **Paleta Lateral e Inspetor (Estilo Lucidchart / Task Pane)**:
   - Barra lateral colapsável com formas rápidas (`▢ Entidade Forte`, `⧉ Entidade Fraca`, `◇ Relacionamento`).
   - Inspetor de seleção ao vivo: exibe propriedades da entidade ou relacionamento selecionado e atalho para edição.
3. **Barra de Status Inferior (Excel)**:
   - Indicador de status `Pronto`, atalhos de alternância rápida de notação e controles de zoom com porcentagem.

---

## 🎯 Ajuste e Espaçamento de Atributos

- **Arrastar Atributos Diretamente no Canvas**: clique em qualquer elipse de atributo (ou sub-atributo de composto) e arraste-o livremente ao redor da entidade. A linha de conexão segue dinamicamente a posição escolhida.
- **Slider de Espaçamento Global**: na aba *Exibir & Layout*, ajuste o raio de distância dos atributos em tempo real ou use os presets:
  - *Compacto* (65px)
  - *Normal* (90px)
  - *Amplo* (135px)
- **Auto-Organizar Atributos**: botão `🔄 Auto-Organizar` reorganiza automaticamente todos os atributos em leque ao redor das entidades, eliminando sobreposições.
- **Ajuste Individual na Entidade**: na janela de edição da entidade, também é possível definir o espaçamento específico para aquela entidade ou redefinir suas posições.

---

## 📐 Duas Notações Integradas: Chen & Tabelas

Alterne instantaneamente entre as duas visões com um clique:
- **Notação de Chen (Conceitual - Navathe)**:
  - Entidades regulares em retângulo simples e entidades fracas em retângulo duplo.
  - Relacionamentos regulares em losango simples e identificadores em losango duplo.
  - Atributos em elipses: simples, chave primária (sublinhado sólido), chave parcial (sublinhado tracejado), multivalorado (elipse dupla), derivado (tracejada) e composto (ramificado).
  - Participação total representada por linhas duplas paralelas e parcial por linha simples.
  - Rótulos de cardinalidades (`1`, `N`, `M`) com halo protetor branco e papéis (*Roles*) recursivos.
- **Diagrama de Tabelas (Lógico / Relacional)**:
  - Tabelas relacionais com cabeçalho colorido, ícones e linhas divisórias.
  - Atributos com ícones de chave (`🔑 PK`, `🏷️ PK Parcial`, `⭕ Multi`, `⚡ Deriv`, `🌳 Comp`) e indicação de `NOT NULL`.
  - Vínculos relacionais com linhas ortogonais/direcionais conectando as chaves estrangeiras entre tabelas.

---

## 🏛 Exemplo do Livro Incluso (Figura 3.2 - Esquema Empresa)

O aplicativo inclui o esquema clássico completo da **Figura 3.2** do livro de Navathe:
- **`FUNCIONARIO`**: Atributo composto `Nome` (`Pnome`, `Minicial`, `Unome`), `Cpf` (PK), `Datanasc`, `Endereco`, `Salario`, `Sexo`.
- **`DEPARTAMENTO`**: `Nome` (PK), `Numero` (PK), `Localizacoes` (Multivalorado), `Numero_funcionarios` (Derivado).
- **`PROJETO`**: `Nome` (PK), `Numero` (PK), `Localizacao`.
- **`DEPENDENTE`**: Entidade Fraca com `Nome` (Chave Parcial), `Sexo`, `Data_nascimento`, `Parentesco`.
- **Relacionamentos**:
  - `SUPERVISAO`: Auto-relacionamento 1:N com papéis `Supervisor` (1) e `Supervisionado` (N).
  - `TRABALHA_PARA`: 1:N entre `DEPARTAMENTO` (1) e `FUNCIONARIO` (N).
  - `GERENCIA`: 1:1 entre `FUNCIONARIO` (1, parcial) e `DEPARTAMENTO` (1, total) com atributo `Data_inicio`.
  - `CONTROLA`: 1:N entre `DEPARTAMENTO` (1) e `PROJETO` (N, total).
  - `TRABALHA_EM`: M:N entre `FUNCIONARIO` (M) e `PROJETO` (N, total) com atributo `Horas`.
  - `DEPENDENTES_DE`: Relacionamento Identificador (losango duplo) 1:N entre `FUNCIONARIO` (1) e `DEPENDENTE` (N, total).

---

## 🖱 Navegação e Interação com o Mouse (Zoom & Pan)

- **Pan (Mover o Modelo)**:
  - Arraste o fundo do canvas com o **botão esquerdo** do mouse (estilo Figma/Miro).
  - Ou clique e arraste com o **botão do meio** (roda) ou **botão direito**.
  - Rolagem normal da roda do mouse rola verticalmente; `Shift + Scroll` rola horizontalmente.
- **Zoom In & Zoom Out**:
  - `Ctrl + Roda do Mouse`: aproxima ou afasta a visualização centrado exatamente na posição do cursor do mouse.
  - Botões na faixa de opções e na barra de status: **`🔍+`**, **`🔍−`**, **`100%`** e **`⛶ Ajustar Tudo`**.
- **Mover Elementos**:
  - Arraste entidades, losangos de relacionamentos e elipses de atributos livremente pelo canvas.
- **Editar Elementos**:
  - Duplo-clique sobre uma entidade, losango ou atributo para abrir o editor detalhado.

---

## 🚀 Como Executar

### Requisitos
- Python 3.9+ (com suporte a Tkinter já incluído no instalador do Windows)
- Opcional: `reportlab` para exportação em PDF (`pip install reportlab`)

```bash
# Ativar o ambiente virtual
.venv/Scripts/activate

# Instalar dependências opcionais
pip install -r requirements.txt

# Executar a aplicação
python main.py
```
