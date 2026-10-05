// ═══════════════════════════════════════════════════════════════════════════
//  PI6 – Chopp & Cia | Interações da página e simulador de risco
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', () => {
	const poucoMovimento = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	AOS.init({ once: true, duration: 650, disable: poucoMovimento });

	iniciarNavegacao();
	iniciarModalGraficos();

	const cfgEl = document.getElementById('config-simulador');
	if (cfgEl) iniciarSimulador(JSON.parse(cfgEl.textContent));
});

// ── Navegação ───────────────────────────────────────────────────────────────
function iniciarNavegacao() {
	const secoes = document.querySelectorAll('section[id]');
	const links = document.querySelectorAll(".navbar .nav-link[href^='#']");
	const topo = document.querySelector('.voltar-topo');
	const menu = document.getElementById('navbarNav');

	const aoRolar = () => {
		let atual = '';
		secoes.forEach((s) => {
			if (window.scrollY >= s.offsetTop - 120) atual = s.id;
		});
		links.forEach((a) => a.classList.toggle('active', a.getAttribute('href') === '#' + atual));
		topo?.classList.toggle('visivel', window.scrollY > 600);
	};
	window.addEventListener('scroll', aoRolar, { passive: true });
	aoRolar();

	// No celular, fecha o menu ao escolher uma seção
	links.forEach((a) =>
		a.addEventListener('click', () => {
			if (menu?.classList.contains('show')) bootstrap.Collapse.getOrCreateInstance(menu).hide();
		})
	);
}

function iniciarModalGraficos() {
	const abrir = (img) => {
		const alvo = document.getElementById('graficoExpandido');
		alvo.src = img.src;
		alvo.alt = img.alt;
		document.getElementById('graficoTitulo').textContent = img.dataset.titulo || '';
		document.getElementById('graficoLeitura').textContent = img.dataset.leitura || '';
	};
	document.querySelectorAll('.grafico-ampliavel').forEach((img) => {
		img.addEventListener('click', () => abrir(img));
		img.addEventListener('keydown', (e) => {
			if (e.key !== 'Enter' && e.key !== ' ') return;
			e.preventDefault();
			abrir(img);
			bootstrap.Modal.getOrCreateInstance(document.getElementById('modalGrafico')).show();
		});
	});
}

// ── Formatação ──────────────────────────────────────────────────────────────
const esc = (v) =>
	String(v ?? '').replace(
		/[&<>"']/g,
		(c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]
	);

const numero = (v, casas = 0) =>
	Number(v || 0).toLocaleString('pt-BR', {
		minimumFractionDigits: casas,
		maximumFractionDigits: casas,
	});

function formatar(valor, tipo) {
	switch (tipo) {
		case 'brl':
			return 'R$ ' + numero(valor, 2);
		case 'pct':
			return numero(Number(valor) * 100) + '%';
		case 'dias':
			return numero(valor) + ' dias';
		default:
			return Number(valor || 0).toLocaleString('pt-BR', { maximumFractionDigits: 1 });
	}
}

const titulo = (s) =>
	String(s)
		.toLowerCase()
		.replace(/(^|\s)\S/g, (c) => c.toUpperCase());

// ── Simulador ───────────────────────────────────────────────────────────────
function iniciarSimulador(cfg) {
	const $ = (id) => document.getElementById(id);
	const NIVEIS = { BAIXO: 'baixo', MÉDIO: 'medio', ALTO: 'alto' };
	const ICONES = {
		FREQUENCIA_COMPRAS: 'bi-cart4',
		TICKET_MEDIO: 'bi-receipt',
		TOTAL_GASTO: 'bi-cash-stack',
		DIAS_DESDE_PRIMEIRA_COMPRA: 'bi-calendar-plus',
		DIAS_DESDE_ULTIMA_COMPRA: 'bi-calendar-check',
		TOTAL_PARCELAS: 'bi-file-earmark-text',
		TOTAL_COMODATOS: 'bi-box-seam',
		PCT_COMPRAS_A_PRAZO: 'bi-hourglass-split',
		PRAZO_MEDIO_COMODATO: 'bi-clock',
		VALOR_MEDIO_COMODATO: 'bi-safe',
		PERFIL: 'bi-person-badge',
		CIDADE: 'bi-geo-alt',
		PAGAMENTO: 'bi-credit-card',
	};
	let clienteAtual = null;

	const rotulo = (f) => cfg.specs[f]?.rotulo || titulo(f.replace(/_/g, ' '));
	const tipo = (f) => cfg.specs[f]?.tipo || 'num';
	const valorExibido = (f, v) => (cfg.categoricas.includes(f) ? titulo(v) : formatar(v, tipo(f)));

	// ── Estados do painel de resultado ──────────────────────────────────────
	function mostrar(estado) {
		['placeholder', 'loading', 'baixo', 'medio', 'alto', 'erro'].forEach((n) =>
			$('resultado-' + n).classList.toggle('d-none', n !== estado)
		);
	}
	function mostrarErro(msg) {
		mostrar('erro');
		$('msg-erro').textContent = msg || 'Erro desconhecido.';
	}

	async function chamarApi(url, corpo) {
		const resp = await fetch(url, {
			method: 'POST',
			headers: { 'Content-Type': 'application/json' },
			body: JSON.stringify(corpo),
			credentials: 'same-origin',
		});
		if (resp.status === 401) {
			// Sessão expirou (15 min): volta ao login e retorna ao simulador
			window.location.href = cfg.login_url;
			throw new Error('Sessão expirada. Redirecionando para o login…');
		}
		const dados = await resp.json();
		if (!resp.ok && dados.erro) throw new Error(dados.erro);
		return dados;
	}

	// ── Abas ────────────────────────────────────────────────────────────────
	function trocarAba(modo) {
		const manual = modo === 'manual';
		$('aba-id').classList.toggle('ativa', !manual);
		$('aba-manual').classList.toggle('ativa', manual);
		$('aba-id').setAttribute('aria-selected', String(!manual));
		$('aba-manual').setAttribute('aria-selected', String(manual));
		$('etapa-id').classList.toggle('d-none', manual);
		$('etapa-manual').classList.toggle('d-none', !manual);
		mostrar('placeholder');
		(manual ? document.querySelector('#form-manual .campo-num') : $('input-id-pessoa'))?.focus();
	}

	// ── Resultado ───────────────────────────────────────────────────────────
	function renderizarResultado(d) {
		const nivel = d.nivel_risco;
		const prob = Number(d.probabilidade_pct);
		const cor = { BAIXO: '#198754', MÉDIO: '#fd7e14', ALTO: '#dc3545' }[nivel] || '#dc3545';
		const acao = {
			BAIXO: '<span class="badge bg-success px-3 py-2">Liberar dentro da política padrão</span>',
			MÉDIO:
				'<span class="badge bg-warning text-dark px-3 py-2">Exigir entrada ou acompanhar de perto</span>',
			ALTO: '<span class="badge bg-danger px-3 py-2">Exigir garantias antes de liberar</span>',
		}[nivel];

		const linhas = [...cfg.numericas, ...cfg.categoricas]
			.map(
				(f) =>
					`<tr><td class="text-muted small">${esc(rotulo(f))}</td>` +
					`<td class="text-end fw-semibold small">${esc(valorExibido(f, d.features[f]))}</td></tr>`
			)
			.join('');

		let comparacao = '';
		if (d.modo === 'id') {
			const regra =
				d.risco_regra === null
					? '<span class="text-muted">sem rótulo (fora da população de treino)</span>'
					: d.risco_regra === 1
						? '<span class="text-danger fw-bold">alto risco</span> (atrasou &gt; 20% nos dois lados)'
						: '<span class="text-success fw-bold">bom pagador</span>';
			comparacao = `
				<div class="p-2 rounded-3 bg-light small mb-3">
					<div><i class="bi bi-journal-check me-1 text-chopp"></i><strong>Regra de negócio:</strong> ${regra}</div>
					<div class="text-muted mt-1"><i class="bi bi-info-circle me-1"></i>${
						d.no_treino ? 'Cliente usado no treino do modelo.' : 'Cliente não usado no treino.'
					}</div>
				</div>`;
		}

		const html = `
			<div class="text-center mb-3">
				<div class="fw-bold fs-5">${d.id_pessoa ? 'ID_PESSOA ' + esc(d.id_pessoa) : 'Cliente novo'}</div>
				<div class="text-muted small">${d.modo === 'id' ? 'Perfil carregado da carteira' : 'Simulação manual'}</div>
			</div>
			<div class="mb-3 text-center">
				<div class="text-muted small mb-1">Probabilidade de alto risco</div>
				<div class="display-5 fw-bold" style="color:${cor}">${numero(prob, 1)}%</div>
				<div class="progress mt-2 rounded-pill" style="height:12px;" role="progressbar"
					aria-valuenow="${prob}" aria-valuemin="0" aria-valuemax="100" aria-label="Probabilidade de alto risco">
					<div class="progress-bar rounded-pill" style="width:0%; background:${cor};"></div>
				</div>
				<div class="d-flex justify-content-between text-muted escala-risco">
					<span>0%</span><span>35%</span><span>65%</span><span>100%</span>
				</div>
			</div>
			<div class="text-center mb-3">${acao}</div>
			${comparacao}
			<div class="accordion" id="accordion-feats">
				<div class="accordion-item border-0">
					<h4 class="accordion-header">
						<button class="accordion-button collapsed py-2 small fw-bold" type="button"
							data-bs-toggle="collapse" data-bs-target="#collapse-feats">
							<i class="bi bi-table me-2 text-chopp"></i>Valores usados pelo modelo
						</button>
					</h4>
					<div id="collapse-feats" class="accordion-collapse collapse" data-bs-parent="#accordion-feats">
						<div class="accordion-body p-0 pt-2">
							<table class="table table-sm table-hover mb-0"><tbody>${linhas}</tbody></table>
						</div>
					</div>
				</div>
			</div>
			<div class="mt-3 p-2 rounded-2 bg-light text-center rodape-resultado">
				<i class="bi bi-cpu me-1"></i>${esc(d.modelo_utilizado)} · DATA_VERSION ${esc(d.versao_dados)}
				· MCC de teste ${numero(d.mcc_holdout, 3)} · limiar ${Math.round(d.threshold_usado * 100)}%
			</div>
			<div class="mt-2 small text-muted text-center">
				<i class="bi bi-person-check me-1"></i>Apoio à decisão: a liberação final é de uma pessoa (LGPD, art. 20).
			</div>`;

		const alvo = NIVEIS[nivel] || 'alto';
		$('body-' + alvo).innerHTML = html;
		mostrar(alvo);
		// Lê offsetWidth para forçar o reflow com largura 0; sem isso o navegador
		// pode aplicar a largura final direto, sem a transição.
		const barra = $('body-' + alvo).querySelector('.progress-bar');
		void barra.offsetWidth;
		barra.style.width = Math.min(100, prob) + '%';
		if (window.innerWidth < 992) {
			$('resultado-' + alvo).scrollIntoView({ behavior: 'smooth', block: 'start' });
		}
	}

	// ── Busca por ID ────────────────────────────────────────────────────────
	function avisar(msg) {
		$('alerta-nao-encontrado').classList.toggle('d-none', !msg);
		$('msg-nao-encontrado').textContent = msg || '';
	}

	async function buscarCliente() {
		const id = parseInt($('input-id-pessoa').value, 10);
		if (!id || id <= 0) {
			avisar('Informe um ID válido (número inteiro positivo).');
			return;
		}
		const btn = $('btn-buscar');
		btn.disabled = true;
		btn.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Buscando…';
		avisar('');

		try {
			const d = await chamarApi('/api/buscar_cliente', { id_pessoa: id });
			if (d.encontrado) {
				clienteAtual = d;
				exibirClienteEncontrado(d);
			} else {
				$('etapa-encontrado').classList.add('d-none');
				avisar(
					d.erro ||
						`ID ${id} não está na carteira. Use a aba “Cliente novo” para simular um cliente sem histórico.`
				);
			}
		} catch (e) {
			avisar(e.message || 'Erro de conexão com o servidor.');
		} finally {
			btn.disabled = false;
			btn.innerHTML = '<i class="bi bi-search me-1"></i>Buscar';
		}
	}

	function exibirClienteEncontrado(d) {
		$('id-cliente-display').textContent = `ID_PESSOA ${d.id_pessoa}`;
		$('cards-features').innerHTML = [...cfg.numericas, ...cfg.categoricas]
			.map(
				(f) => `
				<div class="col-6 col-md-4">
					<div class="p-2 rounded-2 border text-center bg-light h-100">
						<i class="bi ${ICONES[f] || 'bi-dot'} text-chopp small" aria-hidden="true"></i>
						<div class="text-muted card-feat-rotulo">${esc(rotulo(f))}</div>
						<div class="fw-bold small">${esc(valorExibido(f, d.features[f]))}</div>
					</div>
				</div>`
			)
			.join('');
		$('etapa-encontrado').classList.remove('d-none');
		mostrar('placeholder');
	}

	function limpar() {
		$('etapa-encontrado').classList.add('d-none');
		avisar('');
		$('input-id-pessoa').value = '';
		clienteAtual = null;
		mostrar('placeholder');
		$('input-id-pessoa').focus();
	}

	// ── Predição ────────────────────────────────────────────────────────────
	async function analisar(corpo, btn) {
		mostrar('loading');
		btn.disabled = true;
		try {
			renderizarResultado(await chamarApi('/api/predict', corpo));
		} catch (e) {
			mostrarErro(e.message);
		} finally {
			btn.disabled = false;
		}
	}

	function analisarManual(e) {
		e.preventDefault();
		const features = {};
		let primeiroInvalido = null;
		document.querySelectorAll('#form-manual .campo-num').forEach((el) => {
			const v = parseFloat(el.value);
			const max = el.max ? parseFloat(el.max) : Infinity;
			const ok = el.value !== '' && !Number.isNaN(v) && v >= 0 && v <= max;
			el.classList.toggle('is-invalid', !ok);
			if (!ok) {
				primeiroInvalido ??= el;
				return;
			}
			// O modelo recebe a fração; o usuário digita o percentual
			features[el.dataset.feature] = el.dataset.tipo === 'pct' ? v / 100 : v;
		});
		if (primeiroInvalido) {
			primeiroInvalido.focus();
			return;
		}
		document.querySelectorAll('#form-manual .campo-cat').forEach((el) => {
			features[el.dataset.feature] = el.value;
		});
		analisar({ modo: 'manual', features }, $('btn-analisar-manual'));
	}

	function preencherExemplo() {
		document.querySelectorAll('#form-manual .campo-num').forEach((el) => {
			el.value = cfg.specs[el.dataset.feature]?.ex ?? '';
			el.classList.remove('is-invalid');
		});
	}

	$('aba-id').addEventListener('click', () => trocarAba('id'));
	$('aba-manual').addEventListener('click', () => trocarAba('manual'));
	$('btn-buscar').addEventListener('click', buscarCliente);
	$('input-id-pessoa').addEventListener('keydown', (e) => {
		if (e.key === 'Enter') buscarCliente();
	});
	$('btn-analisar-id').addEventListener('click', () => {
		if (clienteAtual)
			analisar({ modo: 'id', id_pessoa: clienteAtual.id_pessoa }, $('btn-analisar-id'));
	});
	$('btn-limpar').addEventListener('click', limpar);
	$('form-manual').addEventListener('submit', analisarManual);
	$('form-manual').addEventListener('input', (e) => e.target.classList.remove('is-invalid'));
	$('btn-exemplo').addEventListener('click', preencherExemplo);
}
