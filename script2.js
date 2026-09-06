
document.addEventListener("DOMContentLoaded", () => {
    const sistema = document.getElementById("sistemaMatriz");
    const matriz = document.getElementById("matrizReferencia");
    const descritor = document.getElementById("descritorSaeb");
    const disciplina = document.getElementById("disciplinaQuestao");
    const etapa = document.getElementById("etapaEnsino");
    const ano = document.getElementById("anoSerie");
    const ajuda = document.getElementById("ajudaMatriz");
    if (!sistema || !matriz || !descritor) return;

    const iniciais = {
        sistema: sistema.dataset.valorInicial || sistema.value || "",
        matriz: matriz.dataset.valorInicial || "",
        descritor: descritor.dataset.valorInicial || ""
    };
    let controle = null;

    function option(valor, texto = valor) {
        const item = document.createElement("option");
        item.value = valor;
        item.textContent = texto;
        return item;
    }
    function preencher(select, itens, placeholder, selecionado = "") {
        select.innerHTML = "";
        select.appendChild(option("", placeholder));
        for (const item of itens || []) {
            select.appendChild(option(item.valor ?? item.id, item.texto ?? item.nome ?? item.valor));
        }
        if (selecionado && [...select.options].some(op => op.value === String(selecionado))) {
            select.value = String(selecionado);
        }
    }
    function contextoValido() {
        return sistema.value && disciplina.value && etapa.value && ano.value;
    }
    async function consultar(matrizId = "") {
        if (controle) controle.abort();
        controle = new AbortController();
        const params = new URLSearchParams({
            sistema: sistema.value,
            componente: disciplina.value,
            etapa: etapa.value,
            ano_serie: ano.value
        });
        if (matrizId) params.set("matriz_id", matrizId);
        const resposta = await fetch(`/api/matrizes/opcoes?${params}`, {signal: controle.signal});
        const dados = await resposta.json();
        if (!resposta.ok) throw new Error(dados.erro || "Erro ao consultar a matriz selecionada.");
        return dados;
    }
    async function carregarMatrizes(restaurar = false) {
        if (!contextoValido()) {
            preencher(matriz, [], "Selecione a matriz, o componente, a etapa e o ano/série");
            preencher(descritor, [], "Selecione primeiro a matriz de referência");
            matriz.disabled = true; descritor.disabled = true;
            ajuda.textContent = sistema.value ? "Selecione componente, etapa e ano/série." : "Selecione a matriz de avaliação.";
            return;
        }
        ajuda.textContent = "Carregando matriz de referência...";
        try {
            const dados = await consultar();
            preencher(matriz, dados.matrizes || [], "Selecione a matriz de referência", restaurar ? iniciais.matriz : "");
            matriz.disabled = !(dados.matrizes || []).length;
            preencher(descritor, [], "Selecione primeiro a matriz de referência");
            descritor.disabled = true;
            ajuda.textContent = dados.mensagem || ((dados.matrizes || []).length ? "Selecione a matriz de referência." : "Nenhuma matriz disponível.");
            if ((restaurar && matriz.value) || (dados.matrizes || []).length === 1) {
                if (!matriz.value && dados.matrizes.length === 1) matriz.value = String(dados.matrizes[0].id);
                await carregarDescritores(restaurar);
            }
        } catch (erro) {
            if (erro.name !== "AbortError") ajuda.textContent = erro.message;
        }
    }
    async function carregarDescritores(restaurar = false) {
        if (!matriz.value) {
            preencher(descritor, [], "Selecione primeiro a matriz de referência");
            descritor.disabled = true; return;
        }
        ajuda.textContent = "Carregando descritores...";
        try {
            const dados = await consultar(matriz.value);
            preencher(descritor, dados.descritores || [], "Selecione o descritor", restaurar ? iniciais.descritor : "");
            descritor.disabled = !(dados.descritores || []).length;
            ajuda.textContent = (dados.descritores || []).length
                ? `${dados.descritores.length} descritor(es) encontrado(s).`
                : (dados.mensagem || "Nenhum descritor encontrado.");
        } catch (erro) {
            if (erro.name !== "AbortError") ajuda.textContent = erro.message;
        }
    }
    sistema.addEventListener("change", () => { matriz.value=""; descritor.value=""; carregarMatrizes(false); });
    [disciplina, etapa, ano].forEach(el => el.addEventListener("change", () => {
        matriz.value=""; descritor.value=""; carregarMatrizes(false);
    }));
    matriz.addEventListener("change", () => carregarDescritores(false));
    if (iniciais.sistema) sistema.value = iniciais.sistema;
    carregarMatrizes(true);
});
