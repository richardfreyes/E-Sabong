import { Injectable, inject } from '@angular/core';

import type { PlaybackSource, StreamBrief } from '../arena/video-stage';
import type {
  Broadcast,
  ObsCredentials,
  OperatorAccount,
  OperatorOverview,
  OperatorsOverview,
  Page,
  PublishGrant,
  Recording,
  RecordingAccess,
  RecordingStatus,
  Resolution,
  Uuid,
} from '../models';
import { ApiClient } from './api-client';

/** Stream Operator dashboard API: only streams assigned to the caller (administrators: all). */
@Injectable({ providedIn: 'root' })
export class OperatorApi {
  private readonly api = inject(ApiClient);

  overview = () => this.api.get<OperatorOverview>('/operator/overview');
  startBroadcast = (streamId: Uuid, resolution: Resolution, takeover = false) =>
    this.api.post<PublishGrant>(`/operator/streams/${streamId}/broadcast`, { resolution, takeover });
  /** Fresh publish URL for the caller's own open broadcast (after a connection drop). */
  broadcastToken = (streamId: Uuid) => this.api.post<PublishGrant>(`/operator/streams/${streamId}/broadcast/token`);
  stopBroadcast = (streamId: Uuid) => this.api.post<Broadcast | null>(`/operator/streams/${streamId}/broadcast/stop`);
  obsKey = (streamId: Uuid) => this.api.post<ObsCredentials>(`/operator/streams/${streamId}/obs-key`);
  reconnect = (streamId: Uuid) => this.api.post<void>(`/operator/streams/${streamId}/reconnect`);
  preview = (streamId: Uuid) =>
    this.api.post<{ status: string; message: string; playback: PlaybackSource | null; stream: StreamBrief | null }>(
      `/operator/streams/${streamId}/preview`,
    );
  startRecording = (streamId: Uuid) => this.api.post<Recording>(`/operator/streams/${streamId}/recordings`);
  stopRecording = (recordingId: Uuid) => this.api.post<Recording>(`/operator/recordings/${recordingId}/stop`);
  /** Streaming sessions on the caller's streams (and the caller's own), newest first. */
  broadcasts = (q: { page?: number; page_size?: number }) => this.api.get<Page<Broadcast>>('/operator/broadcasts', q);
  recordings = (q: { page?: number; page_size?: number; stream_id?: Uuid; status?: RecordingStatus }) =>
    this.api.get<Page<Recording>>('/operator/recordings', q);
  recordingAccess = (recordingId: Uuid) => this.api.post<RecordingAccess>(`/operator/recordings/${recordingId}/access`);
  deleteRecording = (recordingId: Uuid) => this.api.delete<void>(`/operator/recordings/${recordingId}`);

  // Administration
  operators = () => this.api.get<OperatorsOverview>('/admin/operators');
  assignStreams = (userId: Uuid, streamIds: Uuid[]) =>
    this.api.put<OperatorAccount>(`/admin/operators/${userId}/streams`, { stream_ids: streamIds });
}
