import { useCallback, useEffect, useRef } from "react";
import { useFreeBrowseStore } from "@/store";
import type { NiiVue } from "@niivue/niivue";
import type { FileItem } from "@/components/file-list";
import { applyLabelColormapsAfterLoad } from "@/hooks/use-volumes";
import {
  deploymentConfig,
  getFreeBrowseConfig,
  type FreeBrowseConfig,
} from "@/lib/deployment-config";
import {
  assertNotLegacyDocument,
  assertSelfContainedNvd,
  prepareNvdBytes,
  stripLoadedDocumentUrls,
} from "@/lib/load-file";

export function useFileLoading(
  nvRef: React.RefObject<NiiVue | null>,
  applyViewerOptions: () => void,
  syncViewerOptionsFromNiiVue: () => void,
  updateSurfaceDetails: () => void,
  instanceConfig?: Readonly<FreeBrowseConfig>,
) {
  const hasInstanceConfig =
    instanceConfig != null && typeof instanceConfig === "object";
  const effectiveConfig = hasInstanceConfig
    ? instanceConfig
    : getFreeBrowseConfig();
  const showUploader = useFreeBrowseStore((s) => s.showUploader);
  const setShowUploader = useFreeBrowseStore((s) => s.setShowUploader);
  const currentImageIndex = useFreeBrowseStore((s) => s.currentImageIndex);
  const setCurrentImageIndex = useFreeBrowseStore(
    (s) => s.setCurrentImageIndex,
  );
  const volumeVersion = useFreeBrowseStore((s) => s.volumeVersion);
  const incrementVolumeVersion = useFreeBrowseStore(
    (s) => s.incrementVolumeVersion,
  );
  const currentSurfaceIndex = useFreeBrowseStore((s) => s.currentSurfaceIndex);
  const setCurrentSurfaceIndex = useFreeBrowseStore(
    (s) => s.setCurrentSurfaceIndex,
  );
  const setActiveTab = useFreeBrowseStore((s) => s.setActiveTab);

  const serverlessMode = hasInstanceConfig
    ? instanceConfig.backend === null
    : deploymentConfig.serverless;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const surfaceFileInputRef = useRef<HTMLInputElement>(null);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  // Load a niivue Document (.nvd) into the scene.
  //
  // Accepts either raw bytes (from a file upload / server fetch) or an
  // already-parsed JSON object (the embedded single-file path). niivue sniffs
  // JSON vs CBOR itself (NVDocument.deserialize -> looksLikeJSON), so both
  // encodings pass through unchanged and FreeBrowse no longer transcodes. The
  // store follows via documentLoaded/volumeLoaded/meshLoaded (niivue-store-sync).
  const loadNvdData = useCallback(
    async (data: ArrayBuffer | Uint8Array | Record<string, unknown>) => {
      const nv = nvRef.current;
      if (!nv) return;
      void syncViewerOptionsFromNiiVue;
      void updateSurfaceDetails;

      let file: File;
      if (data instanceof ArrayBuffer || data instanceof Uint8Array) {
        const rawBytes = data instanceof Uint8Array ? data : new Uint8Array(data);
        const bytes = prepareNvdBytes(rawBytes);
        assertNotLegacyDocument(bytes);
        if (effectiveConfig.allowExternalDocumentUrls === false) {
          assertSelfContainedNvd(bytes);
        }
        file = new File([bytes], "scene.nvd", {
          type: "application/octet-stream",
        });
      } else {
        // Already-parsed JSON object (embedded __EMBEDDED_NVD_DATA__ path).
        // Serialize first so validation inspects the exact JSON bytes NiiVue
        // will deserialize with its `$num` / `$ta` reviver.
        const jsonBytes = new TextEncoder().encode(JSON.stringify(data));
        assertNotLegacyDocument(jsonBytes);
        if (effectiveConfig.allowExternalDocumentUrls === false) {
          assertSelfContainedNvd(jsonBytes);
        }
        file = new File([jsonBytes], "scene.nvd", {
          type: "application/json",
        });
      }

      // `fill: "current"`: a setting the document OMITS keeps the instance's
      // value. v9 documents are sparse (a setting equal to its default is not
      // written), and niivue's default policy would reset every omitted setting
      // to its built-in default, silently changing e.g. crosshair colour or view
      // mode on every load. A document should only change what it sets.
      try {
        await nv.loadDocument(file, { fill: "current" });
      } finally {
        if (effectiveConfig.allowExternalDocumentUrls === false) {
          stripLoadedDocumentUrls(nv);
        }
      }
      // Migrated legacy documents name label palettes by `colormap` only.
      await applyLabelColormapsAfterLoad(nv);

      // Select a first volume / surface so the details tabs have a target. The
      // lists themselves follow via events.
      if (currentImageIndex === null && nv.volumes.length > 0) {
        setCurrentImageIndex(0);
      }
      if (currentSurfaceIndex === null && nv.meshes.length > 0) {
        setCurrentSurfaceIndex(0);
      }
    },
    [
      nvRef,
      effectiveConfig.allowExternalDocumentUrls,
      syncViewerOptionsFromNiiVue,
      updateSurfaceDetails,
      currentImageIndex,
      currentSurfaceIndex,
      setCurrentImageIndex,
      setCurrentSurfaceIndex,
    ],
  );

  // Add uploaded files to NiiVue
  const handleFileUpload = useCallback(
    async (files: File[]) => {
      if (!nvRef.current) return;
      const nv = nvRef.current;

      if (showUploader) {
        setShowUploader(false);
      }

      let retries = 0;
      while (!nv.canvas && retries < 20) {
        console.log(
          `Waiting for canvas to be ready for file upload... attempt ${retries + 1}`,
        );
        await new Promise((resolve) => setTimeout(resolve, 100));
        retries++;
      }

      if (!nv.canvas) {
        throw new Error("Canvas failed to initialize after 2 seconds");
      }

      const nvdFiles = files.filter(
        (file) =>
          file.name.toLowerCase().endsWith(".nvd") ||
          file.name.toLowerCase().endsWith(".json"),
      );

      if (nvdFiles.length > 0) {
        const nvdFile = nvdFiles[0];
        try {
          // Pass raw bytes: loadNvdData sniffs JSON vs CBOR.
          const bytes = await nvdFile.arrayBuffer();
          if (!mountedRef.current) return;
          console.log("NVD file loaded:", nvdFile.name, bytes.byteLength, "bytes");
          await loadNvdData(bytes);
        } catch (error) {
          console.error("Error loading uploaded NVD file:", error);
        }
      } else {
        if (!mountedRef.current) return;
        const promises = files.map(async (file) => {
          // niivue-mono: addVolume accepts a File directly (url: string | File);
          // the separate NVImage.loadFromFile step is gone.
          await nv.addVolume({ url: file, name: file.name });
        });

        await Promise.all(promises);
        if (!mountedRef.current) return;

        applyViewerOptions();
        incrementVolumeVersion();

        if (currentImageIndex === null && files.length > 0) {
          setCurrentImageIndex(0);
        }
      }
    },
    [
      nvRef,
      showUploader,
      currentImageIndex,
      loadNvdData,
      applyViewerOptions,
      incrementVolumeVersion,
      setShowUploader,
      setCurrentImageIndex,
    ],
  );

  const handleImagingFileSelect = useCallback(
    async (file: FileItem) => {
      if (!nvRef.current) return;
      const nv = nvRef.current;

      try {
        if (showUploader) {
          setShowUploader(false);
        }

        let retries = 0;
        while (!nv.canvas && retries < 20) {
          console.log(
            `Waiting for canvas to be ready for imaging file... attempt ${retries + 1}`,
          );
          await new Promise((resolve) => setTimeout(resolve, 100));
          retries++;
        }

        if (!nv.canvas) {
          throw new Error("Canvas failed to initialize after 2 seconds");
        }

        const basename = file.filename.split("/").pop() || file.filename;
        // niivue picks the reader from the extension of `url`, not from
        // `name`, and a blob: URL has none, so anything but NIfTI (the
        // fallback reader) fails to parse. Hand it a named File instead.
        let url: string | File = file.url;
        if (file.url.startsWith("blob:")) {
          const response = await fetch(file.url);
          url = new File([await response.blob()], basename);
        }
        const volume = {
          url,
          name: basename,
        };

        console.log("Adding imaging file to scene:", volume);
        await nv.addVolume(volume);

        applyViewerOptions();
        incrementVolumeVersion();

        if (nv.volumes.length > 0) {
          setCurrentImageIndex(nv.volumes.length - 1);
        }

        console.log("Imaging file loaded successfully");
      } catch (error) {
        console.error("Error loading imaging file:", error);
      }
    },
    [
      nvRef,
      showUploader,
      applyViewerOptions,
      incrementVolumeVersion,
      setShowUploader,
      setCurrentImageIndex,
    ],
  );

  const handleNvdFileSelect = useCallback(
    async (file: FileItem) => {
      if (!nvRef.current) return;
      const nv = nvRef.current;

      try {
        const response = await fetch(file.url);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        // Fetch raw bytes: loadNvdData sniffs JSON vs CBOR.
        const bytes = await response.arrayBuffer();
        console.log(`NVD fetched from ${file.url}: ${bytes.byteLength} bytes`);

        setShowUploader(false);

        let retries = 0;
        while (!nv.canvas && retries < 20) {
          console.log(
            `Waiting for canvas to be ready... attempt ${retries + 1}`,
          );
          await new Promise((resolve) => setTimeout(resolve, 100));
          retries++;
        }

        if (!nv.canvas) {
          throw new Error("Canvas failed to initialize after 2 seconds");
        }

        await loadNvdData(bytes);
      } catch (error) {
        console.error("Error loading NVD:", error);
      }
    },
    [nvRef, loadNvdData, setShowUploader],
  );

  const handleFileChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0) {
        const files = Array.from(e.target.files);
        handleFileUpload(files);
      }
      e.target.value = "";
    },
    [handleFileUpload],
  );

  const handleAddMoreFiles = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleAddSurfaceFiles = useCallback(() => {
    surfaceFileInputRef.current?.click();
  }, []);

  const handleSurfaceFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      if (e.target.files && e.target.files.length > 0 && nvRef.current) {
        const nv = nvRef.current;
        const files = Array.from(e.target.files);

        if (showUploader) {
          setShowUploader(false);
        }

        let retries = 0;
        while (!nv.canvas && retries < 20) {
          await new Promise((resolve) => setTimeout(resolve, 100));
          retries++;
        }

        if (!nv.canvas) {
          console.error("Canvas failed to initialize for surface upload");
          return;
        }

        // addMesh (not loadMeshes) — loadMeshes replaces all meshes; addMesh
        // appends. niivue-mono accepts a File directly (no blob URL), a 0-1
        // `color`, and a shader `shaderType` name. The surfaces list follows via
        // meshLoaded -> event adapter.
        try {
          for (const file of files) {
            await nv.addMesh({
              url: file,
              name: file.name,
              color: [1, 1, 0, 1] as [number, number, number, number],
              shaderType: "phong",
            });
          }
        } catch (error) {
          console.error("Error loading surface files:", error);
        }

        if (currentSurfaceIndex === null && nv.meshes.length > 0) {
          setCurrentSurfaceIndex(nv.meshes.length - files.length);
        }
      }
      e.target.value = "";
    },
    [
      nvRef,
      showUploader,
      currentSurfaceIndex,
      setShowUploader,
      setCurrentSurfaceIndex,
    ],
  );

  // Re-read the volume list after a drag ends (niivue-mono uses the EventTarget
  // API rather than assignable onXxx callback props). The crosshair readout is
  // NOT handled here: `locationChange` reaches the store through the event
  // adapter (niivue-store-sync `onLocationChange`), the single path for it.
  useEffect(() => {
    const nv = nvRef.current;
    if (!nv) return;

    const onDragRelease = () => {
      requestAnimationFrame(() => incrementVolumeVersion());
    };

    nv.addEventListener("dragRelease", onDragRelease);
    return () => {
      nv.removeEventListener("dragRelease", onDragRelease);
    };
  }, [nvRef, incrementVolumeVersion]);

  // niivue's own canvas drag-and-drop: only while the drop zone is up and
  // nothing is loaded, and never when the host switched drag-and-drop off.
  const dragDropEnabled = useFreeBrowseStore((s) => s.dragDropEnabled);
  useEffect(() => {
    void volumeVersion;
    if (nvRef.current) {
      nvRef.current.isDragDropEnabled =
        dragDropEnabled &&
        showUploader &&
        (nvRef.current.volumes?.length ?? 0) === 0;
    }
  }, [nvRef, volumeVersion, showUploader, dragDropEnabled]);

  // If in serverless mode, switch to sceneDetails tab by default
  useEffect(() => {
    if (serverlessMode) {
      setActiveTab("sceneDetails");
    }
  }, [serverlessMode, setActiveTab]);

  // Load NVD from URL parameter on initial load (app only: an embedding host
  // keeps its own query string, see config.readUrlParams).
  useEffect(() => {
    if (!effectiveConfig.readUrlParams) return;
    const urlParams = new URLSearchParams(window.location.search);
    const nvdParam = urlParams.get("nvd");

    if (nvdParam) {
      console.log("Loading NVD from URL parameter:", nvdParam);
      const nvdFromUrl: FileItem = {
        filename: urlParams.get("filename") || nvdParam.split("/").pop() || nvdParam,
        url: nvdParam,
      };
      handleNvdFileSelect(nvdFromUrl);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load volume from URL parameter on initial load (app only, as above).
  useEffect(() => {
    if (!effectiveConfig.readUrlParams) return;
    const urlParams = new URLSearchParams(window.location.search);
    const volParam = urlParams.get("vol");

    if (volParam) {
      console.log("Loading volume from URL parameter:", volParam);
      const filename = urlParams.get("filename") || volParam.split("/").pop() || volParam;
      const fileItem: FileItem = { filename, url: volParam };
      handleImagingFileSelect(fileItem);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load embedded NVD data (for self-contained HTML files; app only, not multi-mount hosts)
  useEffect(() => {
    if (!effectiveConfig.readUrlParams) return;
    const handleEmbeddedNvd = async (event: CustomEvent) => {
      if (!event.detail || !nvRef.current) return;

      if (window.__EMBEDDED_NVD_LOADED__) return;
      window.__EMBEDDED_NVD_LOADED__ = true;

      console.log("Loading embedded NVD data");
      setShowUploader(false);

      let retries = 0;
      while (!nvRef.current.canvas && retries < 20) {
        await new Promise((resolve) => setTimeout(resolve, 100));
        retries++;
      }

      if (nvRef.current.canvas) {
        await loadNvdData(event.detail);
      }
    };

    window.addEventListener(
      "loadEmbeddedNvd",
      handleEmbeddedNvd as unknown as EventListener,
    );

    if (window.__EMBEDDED_NVD_DATA__ && !window.__EMBEDDED_NVD_LOADED__) {
      window.dispatchEvent(
        new CustomEvent("loadEmbeddedNvd", {
          detail: window.__EMBEDDED_NVD_DATA__,
        }),
      );
    }

    return () =>
      window.removeEventListener(
        "loadEmbeddedNvd",
        handleEmbeddedNvd as unknown as EventListener,
      );
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    serverlessMode,
    fileInputRef,
    surfaceFileInputRef,
    loadNvdData,
    handleFileUpload,
    handleImagingFileSelect,
    handleNvdFileSelect,
    handleFileChange,
    handleAddMoreFiles,
    handleAddSurfaceFiles,
    handleSurfaceFileChange,
  };
}
