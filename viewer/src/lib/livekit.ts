import { Room, RoomEvent, createLocalScreenTracks } from "livekit-client";

export type LiveSession = {
  room: Room;
  stopScreenShare: () => Promise<void>;
  disconnect: () => Promise<void>;
};

export async function startLiveSession(opts: {
  url: string;
  token: string;
  onAgentAudio?: (track: MediaStreamTrack) => void;
}): Promise<LiveSession> {
  const room = new Room({ adaptiveStream: true, dynacast: true });
  room.on(RoomEvent.TrackSubscribed, (track) => {
    if (track.kind === "audio" && opts.onAgentAudio) {
      opts.onAgentAudio(track.mediaStreamTrack);
    }
  });

  await room.connect(opts.url, opts.token);
  await room.localParticipant.setMicrophoneEnabled(true);

  // preferCurrentTab is Chromium-only; other browsers fall back to the picker.
  const tracks = await createLocalScreenTracks({
    audio: false,
    resolution: { width: 1280, height: 720, frameRate: 5 },
    preferCurrentTab: true,
  });
  for (const t of tracks) {
    await room.localParticipant.publishTrack(t);
  }

  const stopScreenShare = async () => {
    for (const t of tracks) {
      await room.localParticipant.unpublishTrack(t);
      t.stop();
    }
  };

  return {
    room,
    stopScreenShare,
    disconnect: async () => {
      await stopScreenShare().catch(() => undefined);
      await room.disconnect();
    },
  };
}
