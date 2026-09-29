# modelador_er Chen/EER (Navathe 6ª Ed.) & Notação de Barker

Aplicativo desktop em Python/Tkinter para modelagem conceitual e lógica de banco de dados, com interface moderna inspirada no **Microsoft Office 365 / Excel**. Suporta os padrões formais da **Notação de Chen** (*Sistemas de Banco de Dados*, Ramez Elmasri & Shamkant B. Navathe, 6ª Edição, Capítulo 3) e **Diagramação por Tabelas Relacionais**, com mapeamento ER-para-Relacional completo (Capítulo 9), geração de DDL (PostgreSQL, MySQL, Oracle) e exportação em HTML e PDF[cite: 1].

---

## 👨‍💻 Autor & Contribuição

* **Autor:** Eder Ferreira De Souza
* **LinkedIn:** [linkedin.com/in/ederferreira](https://www.linkedin.com/in/ederferreira)
* **GitHub:** [github.com/ederferreirago](https://github.com/ederferreirago)

Este projeto é **open source** e foi desenvolvido para a comunidade acadêmica e de profissionais de TI. Fique à vontade para **abrir *Issues*** com sugestões/bugs, fazer um ** *Fork*** do repositório e enviar seus ** *Pull Requests*** com melhorias e novas funcionalidades!

---

## 🆕 Novidades desta versão

- **Descrição em tudo**: entidades, atributos, sub-atributos, relacionamentos e domínios têm descrição. No DDL ela vira `COMMENT ON TABLE/COLUMN` (PostgreSQL, Oracle) ou `COMMENT '...'` (MySQL), inclusive nas tabelas associativas, multivaloradas e dimensionais (Kimball)[cite: 1].
- **Todos os elementos de Chen/EER** (aba *Inserir Elementos*): entidade forte/fraca, relacionamento binário, identificador (losango duplo), **n-ário (3+ entidades)**, **especialização, generalização e categoria (união)** com círculo `d`/`o`/`u`, linha simples/dupla (parcial/total), atributo definidor e símbolo ⊂. Cardinalidade em razão (1:N) ou **(mín,máx)**[cite: 1].
- **Notação de Barker** (substitui o antigo "Diagrama de Tabelas"): caixas arredondadas, `#` identificador único, `*` obrigatório, `o` opcional, linha cheia/tracejada (obrigatório/opcional), pé-de-galinha, barra de UID para relacionamentos identificadores, **subtipos aninhados** no supertipo e relacionamento n-ário como entidade de interseção. Projetos antigos com `"notation": "table"` abrem em Barker[cite: 1].
- **Domínios definidos pelo usuário** (`Domínios…`): ex. *Status: 1 = Atualizado, 2 = Desatualizado, 3 = Fechado*. Aceita colar a lista (`1 - Atualizado`, `2 = Desatualizado`, colunas do Excel). O atributo escolhe o domínio e o DDL gera **tabela de domínio + INSERTs + FK** ou apenas **CHECK ... IN (...)**, conforme a opção do domínio[cite: 1].
- **Mapeamento EER no DDL**: uma tabela por classe (8A) ou tabela única com discriminador/flags (8C/8D); categorias com chave substituta; relacionamentos n-ários (passo 7); atributo *Único* (UNIQUE)[cite: 1].
- **Validação do modelo** (F5): entidade sem chave, fraca sem identificador, nomes duplicados, domínios inconsistentes etc. O DDL avisa antes de gerar se houver erros[cite: 1].

## ⌨ Usabilidade

Duplo-clique no fundo cria entidade · botão direito abre menu de contexto (ou arrasta a visão) · Ctrl+Z/Y, Ctrl+D, Del, F2/Enter, setas movem a seleção · *Encaixar na grade* · mover só após 4 px de arrasto · legenda da notação ativa · modos Relacionar/N-ário com aviso na tela e Esc para cancelar · edição de entidade/relacionamento trabalha numa cópia (Fechar/Esc descarta) · F1 mostra todos os atalhos · o salvamento automático grava apenas um **rascunho de recuperação**; o arquivo do projeto só muda com Ctrl+S[cite: 1].

---

## 🖥 Interface

1. **Barra Superior & Faixa de Opções (Ribbon Office 365)**:
   - Barra temática verde Excel com identificação do modelo e status de salvamento automático (`🟢 Salvo localmente`)[cite: 1].
   - Abas da Faixa de Opções:
     - **Página Inicial**: botões de modelagem (`➕ Entidade`, `🔗 Relacionar`, `🗑 Excluir`), seletor segmentado de notação (`📐 Chen / EER` e `📊 Barker`), carregamento do exemplo Navathe e operações de arquivo[cite: 1].
     - **Exibir & Layout**: controle de navegação e zoom (`🔍+`, `🔍−`, `100%`, `⛶ Ajustar Tudo`), **slider de raio/espaçamento dos atributos** (55px a 170px) com presets (*Compacto*, *Normal*, *Amplo*), alternância de grade pontilhada e paleta lateral[cite: 1].
     - **DDL & Banco de Dados**: seleção de SGBD (`PostgreSQL`, `MySQL`, `Oracle`) e geração do script SQL de acordo com os 6 passos formais de Navathe[cite: 1].
     - **Exportar Relatórios**: exportação em HTML clean com layout de cards e exportação em PDF técnico[cite: 1].
2. **Paleta Lateral e Inspetor (Estilo Lucidchart / Task Pane)**:
   - Barra lateral colapsável com formas rápidas (`▢ Entidade Forte`, `⧉ Entidade Fraca`, `◇ Relacionamento`)[cite: 1].
   - Inspetor de seleção ao vivo: exibe propriedades da entidade ou relacionamento selecionado e atalho para edição[cite: 1].
3. **Barra de Status Inferior (Excel)**:
   - Indicador de status `Pronto`, atalhos de alternância rápida de notação e controles de zoom com porcentagem[cite: 1].

---

## 🎯 Ajuste e Espaçamento de Atributos

- **Arrastar Atributos Diretamente no Canvas**: clique em qualquer elipse de atributo (ou sub-atributo de composto) e arraste-o livremente ao redor da entidade. A linha de conexão segue dinamicamente a posição escolhida[cite: 1].
- **Slider de Espaçamento Global**: na aba *Exibir & Layout*, ajuste o raio de distância dos atributos em tempo real ou use os presets:
  - *Compacto* (65px)[cite: 1]
  - *Normal* (90px)[cite: 1]
  - *Amplo* (135px)[cite: 1]
- **Auto-Organizar Atributos**: botão `🔄 Auto-Organizar` reorganiza automaticamente todos os atributos em leque ao redor das entidades, eliminando sobreposições[cite: 1].
- **Ajuste Individual na Entidade**: na janela de edição da entidade, também é possível definir o espaçamento específico para aquela entidade ou redefinir suas posições[cite: 1].

---

## 📐 Duas Notações Integradas: Chen & Tabelas

Alterne instantaneamente entre as duas visões com um clique:
- **Notação de Chen (Conceitual - Navathe)**:
  - Entidades regulares em retângulo simples e entidades fracas em retângulo duplo[cite: 1].
  - Relacionamentos regulares em losango simples e identificadores em losango duplo[cite: 1].
  - Atributos em elipses: simples, chave primária (sublinhado sólido), chave parcial (sublinhado tracejado), multivalorado (elipse dupla), derivado (tracejada) e composto (ramificado)[cite: 1].
  - Participação total representada por linhas duplas paralelas e parcial por linha simples[cite: 1].
  - Rótulos de cardinalidades (`1`, `N`, `M`) com halo protetor branco e papéis (*Roles*) recursivos[cite: 1].
- **Diagrama de Tabelas (Lógico / Relacional)**:
  - Tabelas relacionais com cabeçalho colorido, ícones e linhas divisórias[cite: 1].
  - Atributos com ícones de chave (`🔑 PK`, `🏷️ PK Parcial`, `⭕ Multi`, `⚡ Deriv`, `🌳 Comp`) e indicação de `NOT NULL`[cite: 1].
  - Vínculos relacionais com linhas ortogonais/direcionais conectando as chaves estrangeiras entre tabelas[cite: 1].

---

## 🏛 Exemplo do Livro Incluso (Figura 3.2 - Esquema Empresa)

O aplicativo inclui o esquema clássico completo da **Figura 3.2** do livro de Navathe[cite: 1]:
- **`FUNCIONARIO`**: Atributo composto `Nome` (`Pnome`, `Minicial`, `Unome`), `Cpf` (PK), `Datanasc`, `Endereco`, `Salario`, `Sexo`[cite: 1].
- **`DEPARTAMENTO`**: `Nome` (PK), `Numero` (PK), `Localizacoes` (Multivalorado), `Numero_funcionarios` (Derivado)[cite: 1].
- **`PROJETO`**: `Nome` (PK), `Numero` (PK), `Localizacao`[cite: 1].
- **`DEPENDENTE`**: Entidade Fraca com `Nome` (Chave Parcial), `Sexo`, `Data_nascimento`, `Parentesco`[cite: 1].
- **Relacionamentos**:
  - `SUPERVISAO`: Auto-relacionamento 1:N com papéis `Supervisor` (1) e `Supervisionado` (N)[cite: 1].
  - `TRABALHA_PARA`: 1:N entre `DEPARTAMENTO` (1) e `FUNCIONARIO` (N)[cite: 1].
  - `GERENCIA`: 1:1 entre `FUNCIONARIO` (1, parcial) e `DEPARTAMENTO` (1, total) com atributo `Data_inicio`[cite: 1].
  - `CONTROLA`: 1:N entre `DEPARTAMENTO` (1) e `PROJETO` (N, total)[cite: 1].
  - `TRABALHA_EM`: M:N entre `FUNCIONARIO` (M) e `PROJETO` (N, total) com atributo `Horas`[cite: 1].
  - `DEPENDENTES_DE`: Relacionamento Identificador (losango duplo) 1:N entre `FUNCIONARIO` (1) e `DEPENDENTE` (N, total)[cite: 1].

---

## 🚀 Como Executar

### Requisitos
- Python 3.9+ (com suporte a Tkinter já incluído no instalador do Windows)[cite: 1]
- Opcional: `reportlab` para exportação em PDF (`pip install reportlab`)[cite: 1]

```bash
# Criar variável de ambiente no Python
python -m venv .venv

# Ativar o ambiente virtual
.venv/Scripts/activate

# Instalar dependências opcionais
pip install -r requirements.txt

# Executar a aplicação
python main.py
