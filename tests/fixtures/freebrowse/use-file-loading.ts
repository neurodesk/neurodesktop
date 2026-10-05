import { useCallback, useEffect, useRef } from "react";
import { useFreeBrowseStore } from "@/store";
import { NVDocument, NVImage, type Niivue } from "@niivue/niivue";
import type { FileItem } from "@/components/file-list";

export function useFileLoading(
  nvRef: React.RefObject<Niivue | null>,
  applyViewerOptions: () => void,
  syncViewerOptionsFromNiivue: () => void,
  updateSurfaceDetails: () => void,
  handleLocationChange: (locationObject: any) => void,
  syncDrawingOptionsFromNiivue: () => void,
) {
  const showUploader = useFreeBrowseStore((s) => s.showUploader);
  const setShowUploader = useFreeBrowseStore((s) => s.setShowUploader);
  const currentImageIndex = useFreeBrowseStore((s) => s.currentImageIndex);
  const setCurrentImageIndex = useFreeBrowseStore((s) => s.setCurrentImageIndex);
  const volumeVersion = useFreeBrowseStore((s) => s.volumeVersion);
  const incrementVolumeVersion = useFreeBrowseStore((s) => s.incrementVolumeVersion);
  const currentSurfaceIndex = useFreeBrowseStore((s) => s.currentSurfaceIndex);
  const setCurrentSurfaceIndex = useFreeBrowseStore((s) => s.setCurrentSurfaceIndex);
  const setActiveTab = useFreeBrowseStore((s) => s.setActiveTab);

  const serverlessMode = import.meta.env.VITE_SERVERLESS === 'true';
  const fileInputRef = useRef<HTMLInputElement>(null);
  const surfaceFileInputRef = useRef<HTMLInputElement>(null);

  // Helper function to load NVD data
  const loadNvdData = useCallback(
    async (jsonData: any) => {
      if (!nvRef.current) return;
      const nv = nvRef.current;

      setCurrentImageIndex(null);

      console.log("loadNvdData() -- jsonData: ", jsonData);

      const document = await NVDocument.loadFromJSON(jsonData);
      await document.fetchLinkedData();
      console.log("loadNvdData() document: ", document);

      try {
        await nv.loadDocument(document);

        if (
          jsonData.encodedImageBlobs &&
          jsonData.encodedImageBlobs.length > 0
        ) {
          console.log(
            "Loading encoded image blobs:",
            jsonData.encodedImageBlobs.length,
          );
          for (let i = 0; i < jsonData.encodedImageBlobs.length; i++) {
            const blob = jsonData.encodedImageBlobs[i];
            if (blob) {
              try {
                const imageOptions = jsonData.imageOptionsArray?.[i] || {};
                const nvimage = await NVImage.loadFromBase64({
                  base64: blob,
                  ...imageOptions,
                });
                nv.addVolume(nvimage);
                console.log(
                  `Loaded encoded image blob ${i + 1}/${jsonData.encodedImageBlobs.length}`,
                );
              } catch (error) {
                console.error(`Failed to load encoded image blob ${i}:`, error);
              }
            }
          }
        }

        syncViewerOptionsFromNiivue();
      } catch (error) {
        console.error("nv.loadDocument failed:", error);
        console.log("Current nv.volumes:", nv.volumes);
        console.log("Current nv.meshes:", nv.meshes);
        console.log("Current nv.drawBitmap:", nv.drawBitmap);
        throw error;
      }

      if (jsonData.imageOptionsArray && nv.volumes) {
        for (
          let i = 0;
          i < nv.volumes.length && i < jsonData.imageOptionsArray.length;
          i++
        ) {
          const imageOption = jsonData.imageOptionsArray[i];
          if (imageOption.url) {
            nv.volumes[i].url = imageOption.url;
          }
        }
      }

      if (jsonData.meshes && jsonData.meshes.length > 0) {
        console.log("Loading meshes:", jsonData.meshes);
        await nv.loadMeshes(jsonData.meshes);

        // WORKAROUND: NiiVue's loadLayer doesn't persist layer names on the
        // layer objects it creates. Backfill from the original JSON URLs.
        // Remove after niivue PR that adds `newLayer.name = layerName` is merged.
        for (const meshJson of jsonData.meshes) {
          if (!meshJson.layers || meshJson.layers.length === 0) continue;

          // Match the loaded mesh by name (extracted from URL basename)
          const meshName = meshJson.name || meshJson.url?.split("/").pop() || "";
          const mesh = nv.meshes.find((m: any) => m.name === meshName);
          if (!mesh?.layers) continue;

          for (let li = 0; li < mesh.layers.length && li < meshJson.layers.length; li++) {
            const layerJson = meshJson.layers[li];
            const layerName = layerJson.name || layerJson.url?.split("/").pop() || "";
            if (layerName && !mesh.layers[li].name) {
              mesh.layers[li].name = layerName;
            }
          }
        }
      }

      setCurrentImageIndex(0);
      incrementVolumeVersion();
      updateSurfaceDetails();
      nv.setCrosshairColor([0, 1, 0, 0.1]);
    },
    [nvRef, setCurrentImageIndex, syncViewerOptionsFromNiivue, incrementVolumeVersion, updateSurfaceDetails],
  );

  // Add uploaded files to Niivue
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
          const text = await nvdFile.text();
          const jsonData = JSON.parse(text);
          console.log("NVD data loaded from uploaded file:", jsonData);
          await loadNvdData(jsonData);
        } catch (error) {
          console.error("Error loading uploaded NVD file:", error);
        }
      } else {
        const promises = files.map(async (file) => {
          const nvimage = await NVImage.loadFromFile({
            file: file,
          });
          console.log("nv", nv);
          nv.addVolume(nvimage);
          return nvimage;
        });

        await Promise.all(promises);

        applyViewerOptions();
        incrementVolumeVersion();

        if (currentImageIndex === null && files.length > 0) {
          setCurrentImageIndex(0);
        }
      }
    },
    [nvRef, showUploader, currentImageIndex, loadNvdData, applyViewerOptions, incrementVolumeVersion, setShowUploader, setCurrentImageIndex],
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
        const volume = {
          url: file.url,
          name: basename,
        };

        console.log("Adding imaging file to scene:", volume);
        await nv.addVolumeFromUrl(volume);

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
    [nvRef, showUploader, applyViewerOptions, incrementVolumeVersion, setShowUploader, setCurrentImageIndex],
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
        const jsonData = await response.json();
        console.log("json data returned from server:");
        console.log(jsonData);

        setShowUploader(false);

        let retries = 0;
        while (!nv.canvas && retries < 20) {
          console.log(`Waiting for canvas to be ready... attempt ${retries + 1}`);
          await new Promise((resolve) => setTimeout(resolve, 100));
          retries++;
        }

        if (!nv.canvas) {
          throw new Error("Canvas failed to initialize after 2 seconds");
        }

        await loadNvdData(jsonData);
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

        const meshOptionsList = files.map((file) => ({
          url: URL.createObjectURL(file),
          name: file.name,
          rgba255: [255, 255, 0, 255] as [number, number, number, number],
          meshShaderIndex: 14,
        }));

        try {
          await nv.addMeshesFromUrl(meshOptionsList);
          nv.updateGLVolume();
        } catch (error) {
          console.error("Error loading surface files:", error);
        }

        updateSurfaceDetails();

        if (currentSurfaceIndex === null && nv.meshes.length > 0) {
          setCurrentSurfaceIndex(nv.meshes.length - files.length);
        }
      }
      e.target.value = "";
    },
    [nvRef, showUploader, currentSurfaceIndex, updateSurfaceDetails, setShowUploader, setCurrentSurfaceIndex],
  );

  // Set up Niivue callbacks
  useEffect(() => {
    if (nvRef.current) {
      nvRef.current.onDragRelease = async () => {
        await new Promise((resolve) => requestAnimationFrame(resolve));
        incrementVolumeVersion();
      };

      nvRef.current.onLocationChange = handleLocationChange;
      nvRef.current.onOptsChange = syncDrawingOptionsFromNiivue;
    }
  }, [nvRef, handleLocationChange, syncDrawingOptionsFromNiivue, incrementVolumeVersion]);

  // Enable/disable drag-and-drop based on whether volumes are loaded
  useEffect(() => {
    void volumeVersion;
    if (nvRef.current) {
      nvRef.current.opts.dragAndDropEnabled =
        showUploader && (nvRef.current.volumes?.length ?? 0) === 0;
    }
  }, [nvRef, volumeVersion, showUploader]);

  // If in serverless mode, switch to sceneDetails tab by default
  useEffect(() => {
    if (serverlessMode) {
      setActiveTab("sceneDetails");
    }
  }, [serverlessMode, setActiveTab]);

  // Load NVD from URL parameter on initial load
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const nvdParam = urlParams.get("nvd");

    if (nvdParam) {
      console.log("Loading NVD from URL parameter:", nvdParam);
      const nvdFromUrl: FileItem = {
        filename: nvdParam.split("/").pop() || nvdParam,
        url: nvdParam,
      };
      handleNvdFileSelect(nvdFromUrl);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load volume from URL parameter on initial load
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const volParam = urlParams.get("vol");

    if (volParam) {
      console.log("Loading volume from URL parameter:", volParam);
      const filename = volParam.split("/").pop() || volParam;
      const fileItem: FileItem = { filename, url: volParam };
      handleImagingFileSelect(fileItem);
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Load embedded NVD data (for self-contained HTML files)
  useEffect(() => {
    const handleEmbeddedNvd = async (event: CustomEvent) => {
      if (!event.detail || !nvRef.current) return;

      if ((window as any).__EMBEDDED_NVD_LOADED__) return;
      (window as any).__EMBEDDED_NVD_LOADED__ = true;

      console.log('Loading embedded NVD data');
      setShowUploader(false);

      let retries = 0;
      while (!nvRef.current.canvas && retries < 20) {
        await new Promise(resolve => setTimeout(resolve, 100));
        retries++;
      }

      if (nvRef.current.canvas) {
        await loadNvdData(event.detail);
      }
    };

    window.addEventListener('loadEmbeddedNvd', handleEmbeddedNvd as unknown as EventListener);

    if ((window as any).__EMBEDDED_NVD_DATA__ && !(window as any).__EMBEDDED_NVD_LOADED__) {
      window.dispatchEvent(new CustomEvent('loadEmbeddedNvd', {
        detail: (window as any).__EMBEDDED_NVD_DATA__
      }));
    }

    return () => window.removeEventListener('loadEmbeddedNvd', handleEmbeddedNvd as unknown as EventListener);
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
