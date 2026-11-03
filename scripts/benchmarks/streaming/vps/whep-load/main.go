// whep-load: N concurrent WHEP (WebRTC) viewers that receive every RTP packet and discard it
// (no decoding). Reports join results, received bitrate, RTP loss from sequence gaps (after
// NACK retransmissions), and connection failures as JSON lines.
package main

import (
	"bytes"
	"encoding/json"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"sort"
	"sync"
	"sync/atomic"
	"time"

	"github.com/pion/webrtc/v4"
)

type viewer struct {
	bytes, packets   atomic.Uint64
	joined, failed   atomic.Bool
	firstPacketMs    atomic.Int64
	mu               sync.Mutex
	seen             map[uint32]map[uint64]struct{} // per SSRC: extended sequence numbers seen
	minExt, maxExt   map[uint32]uint64
	lastSeq          map[uint32]uint16
	cycles           map[uint32]uint64
	disconnects      atomic.Int32
}

func (v *viewer) record(ssrc uint32, seq uint16, n int) {
	v.bytes.Add(uint64(n))
	v.packets.Add(1)
	v.mu.Lock()
	defer v.mu.Unlock()
	if _, ok := v.seen[ssrc]; !ok {
		v.seen[ssrc] = map[uint64]struct{}{}
		v.lastSeq[ssrc] = seq
		v.minExt[ssrc] = uint64(seq) + 1<<16
		v.maxExt[ssrc] = uint64(seq) + 1<<16
		v.cycles[ssrc] = 1 << 16
	}
	last := v.lastSeq[ssrc]
	if seq < 0x1000 && last > 0xF000 { // wrap forward
		v.cycles[ssrc] += 1 << 16
	}
	cyc := v.cycles[ssrc]
	if seq > 0xF000 && last < 0x1000 { // late packet from previous cycle
		cyc -= 1 << 16
	}
	ext := cyc + uint64(seq)
	v.seen[ssrc][ext] = struct{}{}
	if ext > v.maxExt[ssrc] {
		v.maxExt[ssrc] = ext
		v.lastSeq[ssrc] = seq
	}
	if ext < v.minExt[ssrc] {
		v.minExt[ssrc] = ext
	}
}

func (v *viewer) loss() (expected, received uint64) {
	v.mu.Lock()
	defer v.mu.Unlock()
	for ssrc, s := range v.seen {
		expected += v.maxExt[ssrc] - v.minExt[ssrc] + 1
		received += uint64(len(s))
	}
	return
}

func join(url string, v *viewer, start time.Time) {
	pc, err := webrtc.NewPeerConnection(webrtc.Configuration{})
	if err != nil {
		v.failed.Store(true)
		return
	}
	for _, k := range []webrtc.RTPCodecType{webrtc.RTPCodecTypeVideo, webrtc.RTPCodecTypeAudio} {
		pc.AddTransceiverFromKind(k, webrtc.RTPTransceiverInit{Direction: webrtc.RTPTransceiverDirectionRecvonly})
	}
	pc.OnConnectionStateChange(func(s webrtc.PeerConnectionState) {
		if s == webrtc.PeerConnectionStateFailed || s == webrtc.PeerConnectionStateDisconnected || s == webrtc.PeerConnectionStateClosed {
			v.disconnects.Add(1)
		}
	})
	pc.OnTrack(func(t *webrtc.TrackRemote, _ *webrtc.RTPReceiver) {
		for {
			p, _, err := t.ReadRTP()
			if err != nil {
				return
			}
			if v.packets.Load() == 0 {
				v.firstPacketMs.Store(time.Since(start).Milliseconds())
			}
			v.record(uint32(t.SSRC()), p.SequenceNumber, len(p.Payload)+12)
		}
	})
	offer, _ := pc.CreateOffer(nil)
	done := webrtc.GatheringCompletePromise(pc)
	pc.SetLocalDescription(offer)
	<-done
	resp, err := http.Post(url, "application/sdp", bytes.NewBufferString(pc.LocalDescription().SDP))
	if err != nil || resp.StatusCode != 201 {
		v.failed.Store(true)
		pc.Close()
		return
	}
	answer, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if err := pc.SetRemoteDescription(webrtc.SessionDescription{Type: webrtc.SDPTypeAnswer, SDP: string(answer)}); err != nil {
		v.failed.Store(true)
		return
	}
	v.joined.Store(true)
}

func pct(xs []float64, p float64) float64 {
	if len(xs) == 0 {
		return 0
	}
	sort.Float64s(xs)
	return xs[int(p*float64(len(xs)-1))]
}

func main() {
	url := flag.String("url", "", "WHEP URL including ?token=")
	n := flag.Int("n", 10, "viewers")
	hold := flag.Duration("hold", 60*time.Second, "measurement window after all joined")
	gap := flag.Duration("gap", 20*time.Millisecond, "delay between joins")
	flag.Parse()
	viewers := make([]*viewer, *n)
	start := time.Now()
	var wg sync.WaitGroup
	for i := range viewers {
		v := &viewer{seen: map[uint32]map[uint64]struct{}{}, minExt: map[uint32]uint64{}, maxExt: map[uint32]uint64{}, lastSeq: map[uint32]uint16{}, cycles: map[uint32]uint64{}}
		viewers[i] = v
		wg.Add(1)
		go func(v *viewer, t time.Time) { defer wg.Done(); join(*url, v, t) }(v, time.Now())
		time.Sleep(*gap)
	}
	wg.Wait()
	joinSec := time.Since(start).Seconds()
	time.Sleep(3 * time.Second) // let every viewer reach steady state
	b0 := make([]uint64, *n)
	for i, v := range viewers {
		b0[i] = v.bytes.Load()
	}
	t0 := time.Now()
	stalls := 0
	prev := append([]uint64(nil), b0...)
	for el := time.Duration(0); el < *hold; el += time.Second {
		time.Sleep(time.Second)
		for i, v := range viewers {
			cur := v.packets.Load()
			if v.joined.Load() && cur == prev[i] {
				stalls++ // a viewer that received nothing for a whole second
			}
			prev[i] = cur
		}
	}
	secs := time.Since(t0).Seconds()
	joined, failed, disc := 0, 0, 0
	var rates, firsts []float64
	var exp, rec uint64
	for i, v := range viewers {
		if v.joined.Load() {
			joined++
		}
		if v.failed.Load() {
			failed++
		}
		disc += int(v.disconnects.Load())
		rates = append(rates, float64(v.bytes.Load()-b0[i])*8/secs/1e6)
		if f := v.firstPacketMs.Load(); f > 0 {
			firsts = append(firsts, float64(f))
		}
		e, r := v.loss()
		exp += e
		rec += r
	}
	total := 0.0
	for _, r := range rates {
		total += r
	}
	lossPct := 0.0
	if exp > 0 {
		lossPct = 100 * float64(exp-rec) / float64(exp)
	}
	out := map[string]any{
		"viewers": *n, "joined": joined, "failed": failed, "disconnects": disc, "joinSeconds": fmt.Sprintf("%.1f", joinSec),
		"firstPacketMsP50": pct(firsts, 0.5), "firstPacketMsP95": pct(firsts, 0.95),
		"receivedMbpsTotal": fmt.Sprintf("%.1f", total), "receivedMbpsPerViewerMin": fmt.Sprintf("%.2f", pct(append([]float64(nil), rates...), 0)),
		"receivedMbpsPerViewerP50": fmt.Sprintf("%.2f", pct(append([]float64(nil), rates...), 0.5)),
		"rtpLossPct": fmt.Sprintf("%.3f", lossPct), "viewerSecondsWithoutPackets": stalls, "holdSeconds": int(secs),
	}
	json.NewEncoder(os.Stdout).Encode(out)
}
