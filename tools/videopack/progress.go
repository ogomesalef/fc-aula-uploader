package main

import (
	"strconv"
	"strings"
)

// ProgressState acumula o que o ffmpeg reporta em `-progress pipe:1`.
//
// O ffmpeg escreve blocos de linhas `chave=valor` terminados por `progress=`,
// então vamos atualizando o estado linha a linha e avisamos quando o bloco fecha.
type ProgressState struct {
	OutTimeSec float64
	Fps        float64
	Speed      float64
	TotalSize  int64
	Done       bool
}

// FeedProgressLine aplica uma linha do ffmpeg ao estado.
// Devolve true quando a linha fecha um bloco (ou seja, dá para emitir progresso).
func FeedProgressLine(line string, st *ProgressState) bool {
	line = strings.TrimSpace(line)
	key, value, ok := strings.Cut(line, "=")
	if !ok {
		return false
	}
	key = strings.TrimSpace(key)
	value = strings.TrimSpace(value)
	switch key {
	case "out_time_us", "out_time_ms":
		// out_time_ms do ffmpeg é, na verdade, microssegundos.
		if micro, err := strconv.ParseInt(value, 10, 64); err == nil && micro >= 0 {
			st.OutTimeSec = float64(micro) / 1_000_000
		}
	case "out_time":
		if sec, ok := parseTimecode(value); ok {
			st.OutTimeSec = sec
		}
	case "fps":
		if v, err := strconv.ParseFloat(value, 64); err == nil {
			st.Fps = v
		}
	case "speed":
		if v, err := strconv.ParseFloat(strings.TrimSuffix(value, "x"), 64); err == nil {
			st.Speed = v
		}
	case "total_size":
		if v, err := strconv.ParseInt(value, 10, 64); err == nil {
			st.TotalSize = v
		}
	case "progress":
		st.Done = value == "end"
		return true
	}
	return false
}

// parseTimecode entende "00:01:23.45".
func parseTimecode(value string) (float64, bool) {
	parts := strings.Split(value, ":")
	if len(parts) != 3 {
		return 0, false
	}
	h, err1 := strconv.ParseFloat(parts[0], 64)
	m, err2 := strconv.ParseFloat(parts[1], 64)
	s, err3 := strconv.ParseFloat(parts[2], 64)
	if err1 != nil || err2 != nil || err3 != nil {
		return 0, false
	}
	return h*3600 + m*60 + s, true
}

// Percent converte o tempo já processado em porcentagem da duração total.
func Percent(outTimeSec float64, durationSec float64) float64 {
	if durationSec <= 0 {
		return 0
	}
	pct := outTimeSec / durationSec * 100
	if pct < 0 {
		return 0
	}
	if pct > 99.9 {
		return 99.9
	}
	return pct
}

// ETASeconds estima o tempo restante pela velocidade relatada pelo ffmpeg.
func ETASeconds(outTimeSec float64, durationSec float64, speed float64) float64 {
	if speed <= 0 || durationSec <= 0 {
		return 0
	}
	restante := durationSec - outTimeSec
	if restante < 0 {
		return 0
	}
	return restante / speed
}
