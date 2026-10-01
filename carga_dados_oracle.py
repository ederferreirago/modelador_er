import oracledb
from faker import Faker
import random

# Inicializa o Faker configurado para o Brasil (gera CPFs e nomes brasileiros reais)
fake = Faker('pt_BR')

# =====================================================================
# CONFIGURAÇÕES DE CONEXÃO COM O ORACLE
# Substitua com os dados do seu banco de dados
# =====================================================================
DB_USER = "dbapp"
DB_PASS = "oracle"
DB_HOST = "localhost"
DB_PORT = 1521
DB_SERVICE = "XEPDB1" # ou XE, dependendo da sua instalação

def main():
    print("Conectando ao banco de dados...")
    try:
        # Modo Thin (não requer Oracle Client instalado na máquina)
        connection = oracledb.connect(
            user=DB_USER,
            password=DB_PASS,
            dsn=f"{DB_HOST}:{DB_PORT}/{DB_SERVICE}"
        )
        cursor = connection.cursor()
        print("Conexão bem-sucedida! Iniciando geração de dados...\n")

        # 1. CRIAR DEPARTAMENTOS
        print("1. Cadastrando 10 Departamentos...")
        dept_ids = []
        for _ in range(10):
            nome_depto = fake.company() + " Dept"
            out_numero = cursor.var(int) # Variável OUT para pegar a Sequence gerada
            
            cursor.callproc('pkg_cadastro_empresa.prc_departamento', [nome_depto, out_numero])
            dept_ids.append(out_numero.getvalue())

        # 2. CRIAR FUNCIONÁRIOS (1.000 Linhas)
        print("2. Cadastrando 1.000 Funcionários...")
        funcionarios_cpfs = []
        
        for _ in range(1000):
            pnome = fake.first_name()
            minicial = fake.random_uppercase_letter()
            unome = fake.last_name()
            cpf = fake.unique.cpf().replace('.', '').replace('-', '') # CPF apenas números
            datanasc = fake.date_of_birth(minimum_age=18, maximum_age=65)
            endereco = fake.address().replace('\n', ', ')[:250]
            salario = round(random.uniform(2500.0, 25000.0), 2)
            sexo = random.choice(['M', 'F'])
            depto_numero = random.choice(dept_ids)
            
            # Chama a procedure do funcionário
            cursor.callproc('pkg_cadastro_empresa.prc_funcionario', [
                pnome, minicial, unome, cpf, datanasc, endereco, 
                salario, sexo, depto_numero, None
            ])
            funcionarios_cpfs.append(cpf)

        # 3. DEFINIR GERENTES DOS DEPARTAMENTOS
        print("3. Promovendo alguns funcionários a Gerentes de Departamento...")
        # Pega 10 CPFs aleatórios para serem gerentes dos 10 departamentos
        gerentes = random.sample(funcionarios_cpfs, len(dept_ids))
        for i, dept_id in enumerate(dept_ids):
            cursor.callproc('pkg_cadastro_empresa.prc_definir_gerente', [dept_id, gerentes[i]])

        # 4. CRIAR PROJETOS
        print("4. Cadastrando 30 Projetos...")
        projeto_ids = []
        for _ in range(30):
            nome_proj = "Projeto " + fake.catch_phrase()
            local_proj = fake.city()
            depto_resp = random.choice(dept_ids)
            out_proj_num = cursor.var(int)
            
            cursor.callproc('pkg_cadastro_empresa.prc_projeto', [nome_proj, local_proj, depto_resp, out_proj_num])
            projeto_ids.append(out_proj_num.getvalue())

        # 5. ALOCAR FUNCIONÁRIOS EM PROJETOS (TRABALHA_EM)
        print("5. Alocando funcionários aos projetos...")
        for cpf in funcionarios_cpfs:
            # Cada funcionário trabalha de 1 a 3 projetos diferentes
            projetos_alocados = random.sample(projeto_ids, random.randint(1, 3))
            for proj_id in projetos_alocados:
                horas = round(random.uniform(10.0, 40.0), 1)
                cursor.callproc('pkg_cadastro_empresa.prc_trabalha_em', [cpf, proj_id, horas])

        # 6. CRIAR DEPENDENTES
        print("6. Adicionando Dependentes...")
        # Sorteia 40% dos funcionários para terem dependentes
        func_com_dependentes = random.sample(funcionarios_cpfs, 400)
        parentescos = ['Filho(a)', 'Cônjuge', 'Pai/Mãe']
        
        for cpf in func_com_dependentes:
            # Cada um terá de 1 a 2 dependentes
            for _ in range(random.randint(1, 2)):
                nome_dep = fake.name()
                sexo_dep = random.choice(['M', 'F'])
                data_nasc_dep = fake.date_of_birth(minimum_age=1, maximum_age=80)
                parentesco = random.choice(parentescos)
                
                cursor.callproc('pkg_cadastro_empresa.prc_dependente', [cpf, nome_dep, sexo_dep, data_nasc_dep, parentesco])

        # Gravar as informações no banco
        connection.commit()
        print("\nCarga finalizada com SUCESSO! Foram inseridas milhares de linhas interligadas.")

    except oracledb.Error as e:
        print(f"Erro no Oracle: {e}")
    finally:
        if 'cursor' in locals():
            cursor.close()
        if 'connection' in locals():
            connection.close()
            print("Conexão fechada.")

if __name__ == "__main__":
    main()