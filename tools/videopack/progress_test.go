package main

import (
	"path/filepath"
	"strings"
	"testing"
)

func TestFeedProgressLineAcumulaBloco(t *testing.T) {
	bloco := `frame=120
fps=59.94
out_time_us=5000000
speed=2.5x
total_size=1048576
progress=continue`

	st := ProgressState{}
	fechou := false
	for _, linha := range strings.Split(bloco, "\n") {
		if FeedProgressLine(linha, &st) {
			fechou = true
		}
	}
	if !fechou {
		t.Fatal("a linha progress= deveria fechar o bloco")
	}
	if st.OutTimeSec != 5 {
		t.Fatalf("out_time_us=5000000 deveria virar 5s, veio %v", st.OutTimeSec)
	}
	if st.Fps != 59.94 {
		t.Fatalf("fps errado: %v", st.Fps)
	}
	if st.Speed != 2.5 {
		t.Fatalf("speed errado: %v", st.Speed)
	}
	if st.TotalSize != 1048576 {
		t.Fatalf("total_size errado: %v", st.TotalSize)
	}
	if st.Done {
		t.Fatal("progress=continue não é o fim")
	}
}

func TestFeedProgressLineFimDeArquivo(t *testing.T) {
	st := ProgressState{}
	if !FeedProgressLine("progress=end", &st) {
		t.Fatal("progress=end deveria fechar o bloco")
	}
	if !st.Done {
		t.Fatal("progress=end deveria marcar Done")
	}
}

func TestFeedProgressLineIgnoraLixo(t *testing.T) {
	st := ProgressState{}
	if FeedProgressLine("linha sem igual", &st) {
		t.Fatal("linha sem = não fecha bloco")
	}
	if FeedProgressLine("out_time_us=N/A", &st) {
		t.Fatal("valor inválido não fecha bloco")
	}
	if st.OutTimeSec != 0 {
		t.Fatalf("valor inválido não deveria mudar o estado: %v", st.OutTimeSec)
	}
}

func TestFeedProgressLineTimecode(t *testing.T) {
	st := ProgressState{}
	FeedProgressLine("out_time=00:01:30.50", &st)
	if st.OutTimeSec != 90.5 {
		t.Fatalf("timecode deveria virar 90.5s, veio %v", st.OutTimeSec)
	}
}

func TestPercent(t *testing.T) {
	if got := Percent(30, 60); got != 50 {
		t.Fatalf("30 de 60 deveria ser 50%%, veio %v", got)
	}
	if got := Percent(10, 0); got != 0 {
		t.Fatalf("duração zero deveria dar 0, veio %v", got)
	}
	// Só o evento de fim declara 100%, para a barra não parar antes da hora.
	if got := Percent(100, 60); got > 99.9 {
		t.Fatalf("progresso não pode passar de 99.9 antes do fim, veio %v", got)
	}
}

func TestETASeconds(t *testing.T) {
	// Faltam 60s de vídeo processando a 2x: 30s de espera.
	if got := ETASeconds(60, 120, 2); got != 30 {
		t.Fatalf("ETA deveria ser 30s, veio %v", got)
	}
	if got := ETASeconds(60, 120, 0); got != 0 {
		t.Fatalf("sem velocidade não dá para estimar, veio %v", got)
	}
}

func TestOutputPathUsaSufixoEMp4(t *testing.T) {
	got := outputPath(filepath.Join("/tmp", "aula-01.mov"), "", " (comprimido)")
	want := filepath.Join("/tmp", "aula-01 (comprimido).mp4")
	if got != want {
		t.Fatalf("saída errada:\n got %s\nwant %s", got, want)
	}
}

func TestOutputPathRespeitaOutDir(t *testing.T) {
	got := outputPath(filepath.Join("/origem", "aula.mp4"), "/destino", " (comprimido)")
	want := filepath.Join("/destino", "aula (comprimido).mp4")
	if got != want {
		t.Fatalf("saída errada:\n got %s\nwant %s", got, want)
	}
}

func TestEncodeArgsMantem1080pEHardware(t *testing.T) {
	opts := PackOptions{Engine: "hw", AudioBps: 128000}
	info := VideoInfo{Arquivo: "/tmp/a.mp4", Largura: 3840, Altura: 2160}
	plan := BitratePlan{VideoBps: 4000000, AudioBps: 128000, MaxrateBps: 5000000, BufsizeBps: 8000000}
	args := strings.Join(encodeArgs(opts, info, plan, "/tmp/out.mp4"), " ")

	if !strings.Contains(args, "h264_videotoolbox") {
		t.Fatalf("engine hw deveria usar videotoolbox: %s", args)
	}
	if !strings.Contains(args, "-allow_sw 1") {
		t.Fatalf("faltou fallback de software do VideoToolbox: %s", args)
	}
	if !strings.Contains(args, "scale=w='min(1920,iw)':h=-2") {
		t.Fatalf("faltou o limite de 1080p: %s", args)
	}
	if !strings.Contains(args, "-progress pipe:1") {
		t.Fatalf("faltou o progresso legível: %s", args)
	}
	if !strings.Contains(args, "+faststart") {
		t.Fatalf("faltou faststart para o vídeo abrir rápido no preview: %s", args)
	}
	if !strings.Contains(args, "-c:a aac") || !strings.Contains(args, "-ar 48000") {
		t.Fatalf("áudio deveria ser AAC 48 kHz: %s", args)
	}
	if strings.Contains(args, "loudnorm") || strings.Contains(args, "volume=") {
		t.Fatalf("não pode alterar o volume do áudio: %s", args)
	}
}

func TestEncodeArgsX264(t *testing.T) {
	opts := PackOptions{Engine: "x264", AudioBps: 128000}
	info := VideoInfo{Arquivo: "/tmp/a.mp4", Largura: 1920, Altura: 1080}
	plan := BitratePlan{VideoBps: 4000000, AudioBps: 128000, MaxrateBps: 5000000, BufsizeBps: 8000000}
	args := strings.Join(encodeArgs(opts, info, plan, "/tmp/out.mp4"), " ")

	if !strings.Contains(args, "libx264") {
		t.Fatalf("engine x264 deveria usar libx264: %s", args)
	}
}
