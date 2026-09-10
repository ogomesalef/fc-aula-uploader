package main

import "testing"

const umGiB int64 = 1 << 30

func TestPlanForTargetAcertaOTamanho(t *testing.T) {
	// Uma hora de vídeo cabendo em 1 GiB.
	plan := PlanForTarget(umGiB, 3600, 128_000, 0)
	previsto := float64(plan.VideoBps+plan.AudioBps) * 3600 / 8
	if previsto > float64(umGiB) {
		t.Fatalf("previsão %.0f bytes passou do alvo %d", previsto, umGiB)
	}
	if previsto < float64(umGiB)*0.95 {
		t.Fatalf("previsão %.0f bytes ficou longe demais do alvo %d", previsto, umGiB)
	}
	if plan.Apertado {
		t.Fatal("uma hora em 1 GiB não deveria ser considerado apertado")
	}
}

func TestPlanForTargetVideoLongoAvisaApertado(t *testing.T) {
	// Dez horas em 1 GiB exigiria um bitrate irreal.
	plan := PlanForTarget(umGiB, 36000, 128_000, 0)
	if !plan.Apertado {
		t.Fatal("vídeo muito longo deveria marcar Apertado")
	}
	if plan.VideoBps < minVideoBps {
		t.Fatalf("bitrate %d ficou abaixo do piso %d", plan.VideoBps, minVideoBps)
	}
}

func TestPlanForTargetNaoUltrapassaOriginal(t *testing.T) {
	// Vídeo curto e leve: não faz sentido inflar para "encher" o alvo.
	plan := PlanForTarget(umGiB, 60, 128_000, 2_000_000)
	if plan.VideoBps > 2_000_000 {
		t.Fatalf("bitrate %d passou do original 2000000", plan.VideoBps)
	}
}

func TestPlanForTargetDuracaoInvalida(t *testing.T) {
	plan := PlanForTarget(umGiB, 0, 128_000, 0)
	if plan.VideoBps <= 0 {
		t.Fatal("duração inválida deveria devolver um bitrate de segurança")
	}
}

func TestNeedsRetry(t *testing.T) {
	teto := int64(1234803097)
	if NeedsRetry(teto-1, teto) {
		t.Fatal("abaixo do teto não deveria repetir")
	}
	if !NeedsRetry(teto+1, teto) {
		t.Fatal("acima do teto deveria repetir")
	}
}

func TestRetryBitrateReduzProporcionalAoExcesso(t *testing.T) {
	anterior := int64(4_000_000)
	// Saiu 50% maior que o alvo: o próximo bitrate tem que cair perto da metade.
	novo := RetryBitrate(anterior, umGiB+umGiB/2, umGiB)
	if novo >= anterior {
		t.Fatalf("bitrate novo %d deveria ser menor que %d", novo, anterior)
	}
	if novo > anterior*70/100 {
		t.Fatalf("bitrate novo %d cortou pouco para um excesso de 50%%", novo)
	}
}

func TestRetryBitrateExcessoPequenoAindaCorta(t *testing.T) {
	anterior := int64(4_000_000)
	novo := RetryBitrate(anterior, umGiB+1000, umGiB)
	if novo >= anterior {
		t.Fatalf("mesmo estourando por pouco o bitrate %d deveria cair", novo)
	}
}

func TestTargetWidthMantem1080p(t *testing.T) {
	if got := TargetWidth(3840); got != 1920 {
		t.Fatalf("4K deveria virar 1920, veio %d", got)
	}
	if got := TargetWidth(1920); got != 1920 {
		t.Fatalf("1080p deveria continuar 1920, veio %d", got)
	}
	if got := TargetWidth(1280); got != 1280 {
		t.Fatalf("720p não pode ser ampliado, veio %d", got)
	}
}
