package main

// Cálculo do bitrate necessário para o arquivo cair no tamanho alvo.
//
// O tamanho final de um MP4 é, na prática, (bitrate_video + bitrate_audio) *
// duração + overhead do container. Invertendo a conta dá para mirar num alvo
// de bytes com boa precisão; o resto é conferir o arquivo gerado e ajustar.

const (
	// Margem para o overhead do container (moov atom, índices, padding).
	containerOverhead = 0.985
	// Abaixo disso 1080p fica visivelmente ruim; avisamos em vez de destruir.
	minVideoBps int64 = 900_000
	// Teto de segurança: não adianta pedir mais do que o original tinha.
	maxVideoBps int64 = 20_000_000
)

// BitratePlan descreve o encode planejado para um arquivo.
type BitratePlan struct {
	VideoBps int64
	AudioBps int64
	// MaxrateBps e BufsizeBps controlam picos para o arquivo não estourar o teto.
	MaxrateBps int64
	BufsizeBps int64
	// Apertado indica que o alvo exigiu o bitrate mínimo: o vídeo é longo
	// demais para caber no alvo sem perder qualidade perceptível.
	Apertado bool
}

// PlanForTarget calcula o bitrate de vídeo para o arquivo caber em targetBytes.
//
// durationSec vem do ffprobe. audioBps é o bitrate reservado para o áudio.
func PlanForTarget(targetBytes int64, durationSec float64, audioBps int64, sourceBps int64) BitratePlan {
	if durationSec <= 0 || targetBytes <= 0 {
		return BitratePlan{VideoBps: minVideoBps, AudioBps: audioBps, MaxrateBps: minVideoBps, BufsizeBps: minVideoBps * 2}
	}
	totalBps := int64(float64(targetBytes) * 8 / durationSec * containerOverhead)
	video := totalBps - audioBps

	apertado := false
	if video < minVideoBps {
		video = minVideoBps
		apertado = true
	}
	if video > maxVideoBps {
		video = maxVideoBps
	}
	// Reencodar acima do bitrate original só incha o arquivo sem ganhar nada.
	if sourceBps > 0 && video > sourceBps {
		video = sourceBps
	}

	return BitratePlan{
		VideoBps:   video,
		AudioBps:   audioBps,
		MaxrateBps: int64(float64(video) * 1.35),
		BufsizeBps: video * 2,
		Apertado:   apertado,
	}
}

// RetryBitrate devolve o bitrate da próxima tentativa quando o arquivo gerado
// passou do teto. Reduz na proporção do excesso, com uma folga extra de 4% para
// não ficar raspando o limite de novo.
func RetryBitrate(previousBps int64, gotBytes int64, targetBytes int64) int64 {
	if gotBytes <= 0 || targetBytes <= 0 || previousBps <= 0 {
		return previousBps
	}
	ratio := float64(targetBytes) / float64(gotBytes)
	if ratio > 0.98 {
		// Passou por pouco: um corte mínimo já resolve.
		ratio = 0.98
	}
	next := int64(float64(previousBps) * ratio * 0.96)
	if next < minVideoBps/2 {
		next = minVideoBps / 2
	}
	return next
}

// NeedsRetry diz se o arquivo gerado estourou o teto rígido.
func NeedsRetry(gotBytes int64, maxBytes int64) bool {
	return maxBytes > 0 && gotBytes > maxBytes
}

// TargetWidth mantém 1080p: reduz o que for maior e nunca amplia o que é menor.
func TargetWidth(sourceWidth int) int {
	if sourceWidth > 1920 {
		return 1920
	}
	return sourceWidth
}
