package main

import (
	"bufio"
	"context"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
)

const maxTentativas = 3

// PackOptions controla o lote de conversão.
type PackOptions struct {
	TargetBytes int64
	MaxBytes    int64
	Jobs        int
	Engine      string // "hw" (VideoToolbox) ou "x264"
	OutSuffix   string
	OutDir      string
	AudioBps    int64
	FFmpeg      string
	FFprobe     string
}

// Pack converte a lista de arquivos respeitando o limite de jobs simultâneos.
//
// Cada worker cuida de um arquivo do começo ao fim (probe, encode, conferência
// de tamanho, reencode se estourou). O semáforo evita que todos os ffmpeg
// disputem o encoder de hardware ao mesmo tempo, que é o que faria a máquina
// engasgar sem ganhar velocidade.
func Pack(ctx context.Context, opts PackOptions, paths []string, em *Emitter) int {
	em.Send(Event{Tipo: "inicio", Total: len(paths)})

	jobs := opts.Jobs
	if jobs < 1 {
		jobs = 1
	}
	sem := make(chan struct{}, jobs)
	var wg sync.WaitGroup
	var mu sync.Mutex
	falhas := 0

	for _, p := range paths {
		wg.Add(1)
		go func(path string) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
			case <-ctx.Done():
				return
			}
			defer func() { <-sem }()

			if err := packOne(ctx, opts, path, em); err != nil {
				if ctx.Err() != nil {
					return
				}
				mu.Lock()
				falhas++
				mu.Unlock()
				em.Send(Event{Tipo: "erro", Arquivo: path, Nome: filepath.Base(path), Erro: err.Error()})
			}
		}(p)
	}
	wg.Wait()

	if ctx.Err() != nil {
		em.Send(Event{Tipo: "cancelado"})
		return 130
	}
	em.Send(Event{Tipo: "lote-fim", Total: len(paths)})
	if falhas > 0 {
		return 1
	}
	return 0
}

func packOne(ctx context.Context, opts PackOptions, path string, em *Emitter) error {
	info := Probe(ctx, opts.FFprobe, path)
	if info.Erro != "" {
		return fmt.Errorf("%s", info.Erro)
	}
	if info.Duracao <= 0 {
		return fmt.Errorf("não consegui descobrir a duração do vídeo")
	}

	saida := outputPath(path, opts.OutDir, opts.OutSuffix)
	plan := PlanForTarget(opts.TargetBytes, info.Duracao, opts.AudioBps, info.Bitrate)

	em.Send(Event{
		Tipo: "arquivo-inicio", Arquivo: path, Nome: info.Nome, Saida: saida,
		TamanhoOld: info.Tamanho, Largura: info.Largura, Altura: info.Altura, Duracao: info.Duracao,
	})
	if plan.Apertado {
		em.Send(Event{
			Tipo: "aviso", Arquivo: path, Nome: info.Nome,
			Aviso: "vídeo longo para o alvo de tamanho: a qualidade pode cair mais que o normal",
		})
	}

	engineOpts := opts
	for tentativa := 1; tentativa <= maxTentativas; tentativa++ {
		if err := runEncode(ctx, engineOpts, info, plan, saida, tentativa, em); err != nil {
			if engineOpts.Engine != "x264" && ctx.Err() == nil {
				em.Send(Event{
					Tipo: "aviso", Arquivo: path, Nome: info.Nome,
					Aviso: "encoder de hardware falhou, tentando x264",
				})
				engineOpts.Engine = "x264"
				if err2 := runEncode(ctx, engineOpts, info, plan, saida, tentativa, em); err2 != nil {
					os.Remove(saida)
					return err2
				}
			} else {
				os.Remove(saida)
				return err
			}
		}
		st, err := os.Stat(saida)
		if err != nil {
			return fmt.Errorf("arquivo convertido não apareceu: %w", err)
		}
		if !NeedsRetry(st.Size(), opts.MaxBytes) {
			largura, altura := info.Largura, info.Altura
			if largura > 1920 && largura > 0 {
				altura = altura * 1920 / largura
				largura = 1920
			}
			em.Send(Event{
				Tipo: "fim", Arquivo: path, Nome: info.Nome, Saida: saida,
				Tamanho: st.Size(), TamanhoOld: info.Tamanho, Tentativa: tentativa,
				Largura: largura, Altura: altura, Duracao: info.Duracao,
			})
			return nil
		}
		if tentativa == maxTentativas {
			os.Remove(saida)
			return fmt.Errorf("não consegui deixar o arquivo abaixo do limite depois de %d tentativas", maxTentativas)
		}
		novo := RetryBitrate(plan.VideoBps, st.Size(), opts.TargetBytes)
		em.Send(Event{
			Tipo: "aviso", Arquivo: path, Nome: info.Nome,
			Aviso: fmt.Sprintf("ficou em %.2f GB, acima do limite: refazendo com bitrate menor", float64(st.Size())/(1<<30)),
		})
		plan.VideoBps = novo
		plan.MaxrateBps = int64(float64(novo) * 1.2)
		plan.BufsizeBps = novo * 2
	}
	return nil
}

func runEncode(ctx context.Context, opts PackOptions, info VideoInfo, plan BitratePlan, saida string, tentativa int, em *Emitter) error {
	args := encodeArgs(opts, info, plan, saida)
	cmd := exec.CommandContext(ctx, opts.FFmpeg, args...)
	// Matar o processo no cancelamento é o que garante que nada fica rodando
	// depois que a interface pede para parar.
	cmd.Cancel = func() error { return cmd.Process.Kill() }

	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return err
	}
	var stderr strings.Builder
	cmd.Stderr = &stderr

	if err := cmd.Start(); err != nil {
		return fmt.Errorf("não consegui iniciar o ffmpeg: %w", err)
	}

	scanner := bufio.NewScanner(stdout)
	scanner.Buffer(make([]byte, 64*1024), 1024*1024)
	st := ProgressState{}
	for scanner.Scan() {
		if !FeedProgressLine(scanner.Text(), &st) {
			continue
		}
		em.Send(Event{
			Tipo: "progresso", Arquivo: info.Arquivo, Nome: info.Nome,
			Pct:       Percent(st.OutTimeSec, info.Duracao),
			Fps:       st.Fps,
			EtaS:      ETASeconds(st.OutTimeSec, info.Duracao, st.Speed),
			Tentativa: tentativa,
		})
	}
	if err := cmd.Wait(); err != nil {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("ffmpeg falhou: %s", ultimaLinha(stderr.String()))
	}
	return nil
}

func encodeArgs(opts PackOptions, info VideoInfo, plan BitratePlan, saida string) []string {
	args := []string{
		"-y", "-nostdin", "-hide_banner",
		"-i", info.Arquivo,
		"-map", "0:v:0", "-map", "0:a?",
	}
	if opts.Engine == "x264" {
		args = append(args,
			"-c:v", "libx264", "-preset", "medium",
			"-b:v", fmt.Sprintf("%d", plan.VideoBps),
			"-maxrate", fmt.Sprintf("%d", plan.MaxrateBps),
			"-bufsize", fmt.Sprintf("%d", plan.BufsizeBps),
		)
	} else {
		args = append(args,
			"-c:v", "h264_videotoolbox",
			"-allow_sw", "1",
			"-realtime", "0",
			"-b:v", fmt.Sprintf("%d", plan.VideoBps),
			"-maxrate", fmt.Sprintf("%d", plan.MaxrateBps),
			"-bufsize", fmt.Sprintf("%d", plan.BufsizeBps),
		)
	}
	// Mantém 1080p: reduz quem é maior, não amplia quem é menor.
	// Áudio AAC stereo em qualidade de aula; aresample só corrige sync —
	// nenhum filtro de volume (loudnorm/volume) para não alterar o nível.
	args = append(args,
		"-vf", "scale=w='min(1920,iw)':h=-2",
		"-pix_fmt", "yuv420p",
		"-c:a", "aac", "-b:a", fmt.Sprintf("%d", plan.AudioBps), "-ac", "2", "-ar", "48000",
		"-af", "aresample=async=1:first_pts=0",
		"-movflags", "+faststart",
		"-progress", "pipe:1", "-nostats",
		saida,
	)
	return args
}

// outputPath monta "aula-01 (comprimido).mp4" ao lado do original (ou em OutDir).
func outputPath(input string, outDir string, suffix string) string {
	dir := filepath.Dir(input)
	if outDir != "" {
		dir = outDir
	}
	base := filepath.Base(input)
	ext := filepath.Ext(base)
	nome := strings.TrimSuffix(base, ext)
	return filepath.Join(dir, nome+suffix+".mp4")
}

func ultimaLinha(texto string) string {
	linhas := strings.Split(strings.TrimSpace(texto), "\n")
	for i := len(linhas) - 1; i >= 0; i-- {
		l := strings.TrimSpace(linhas[i])
		if l != "" {
			return l
		}
	}
	return "erro desconhecido"
}
