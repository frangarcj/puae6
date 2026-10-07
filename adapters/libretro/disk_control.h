// The virtual disk tray behind libretro's Disk Control interface (T17a): which images exist, which one is selected, whether the tray is open, and the
// media events that make the bridge's drive follow what the frontend asked for.
//
// The frontend's calls only record a request here (they answer from this state and touch nothing else). The adapter turns the difference between the
// request and what the bridge's drive holds into p6_media_event_enqueue calls at the start of the next retro_run, so a change always lands on one unit
// boundary, never from inside a callback, and the same calls at the same runs give the same events. No file is read and no clock is looked at.
#ifndef P6_LIBRETRO_DISK_CONTROL_H
#define P6_LIBRETRO_DISK_CONTROL_H

#include <stdint.h>
#include <string>
#include <utility>
#include <vector>

namespace p6_libretro {

struct Image {
    std::string path;
    std::string label;
    uint32_t medium;               // index in the media manifest given to p6_load: images with the same bytes share one
};

class DiskTray {
public:
    // The tray closed with the image `selected` in it (an index past the last one means an empty drive), as a load leaves it.
    void start(std::vector<Image> images, unsigned selected)
    {
        images_ = std::move(images);
        selected_ = selected < images_.size() ? selected : unsigned(images_.size());
        ejected_ = false;
        eject_seen_ = false;
        dirty_ = true;
        in_drive_ = -1;
    }
    void clear() { start({}, 0); dirty_ = false; }

    // --- the frontend's calls: they record, and answer from the record ---
    unsigned count() const { return unsigned(images_.size()); }
    bool ejected() const { return ejected_; }
    unsigned selected() const { return selected_; }
    const Image *image(unsigned index) const { return index < images_.size() ? &images_[index] : nullptr; }

    bool set_ejected(bool ejected)
    {
        if (ejected == ejected_) return true;
        ejected_ = ejected;
        if (ejected) eject_seen_ = true;
        dirty_ = true;
        return true;
    }
    // Only while the tray is open; an index past the last image is the frontend taking the disk out and putting none in.
    bool select(unsigned index)
    {
        if (!ejected_) return false;
        selected_ = index < images_.size() ? index : unsigned(images_.size());
        dirty_ = true;
        return true;
    }
    // Takes an image out of the list (replace_image_index with NULL); the images after it move down by one.
    bool remove(unsigned index)
    {
        if (!ejected_ || index >= images_.size()) return false;
        images_.erase(images_.begin() + index);
        if (selected_ > index) --selected_;
        if (selected_ > images_.size()) selected_ = unsigned(images_.size());
        dirty_ = true;
        return true;
    }

    // --- the boundary: what the bridge has to be told since the last time ---
    struct Change {
        bool mount;                // false: eject
        uint32_t medium;
    };
    // At most two changes, in order: an eject (when the tray was opened since the last boundary and the drive held a disk) and a mount of the selected
    // image (when the tray is closed and the drive does not hold it). Opening and closing the tray on the same disk between two boundaries is still an
    // eject and a mount: the guest sees a disk change, as the frontend asked for.
    unsigned due(Change out[2]) const
    {
        unsigned n = 0;
        if (!dirty_) return 0;
        int64_t drive = in_drive_;
        if (eject_seen_ && drive >= 0) {
            out[n++] = {false, 0};
            drive = -1;
        }
        const int64_t wanted = ejected_ || selected_ >= images_.size() ? -1 : int64_t(images_[selected_].medium);
        if (wanted >= 0 && wanted != drive) out[n++] = {true, uint32_t(wanted)};
        return n;
    }
    void applied(const Change &change) { in_drive_ = change.mount ? int64_t(change.medium) : -1; }
    void settled() { eject_seen_ = false; dirty_ = false; }

    // --- save states (T18): the tray is part of the state, so that a load puts the frontend's notion of the drive where the loaded machine has it ---
    // Everything that decides what the next boundary does: the list (by medium, paths are the session's business), the selection, the open tray, the
    // requests not looked at yet and the medium the bridge's drive holds. A request recorded after the state was taken is not in it, so a load drops it.
    static constexpr uint32_t kNoMedium = UINT32_MAX;
    struct Snapshot {
        std::vector<uint32_t> media;
        uint32_t selected = 0;
        bool ejected = false, eject_seen = false, dirty = false;
        uint32_t in_drive = kNoMedium;
    };
    Snapshot snapshot() const
    {
        Snapshot out;
        for (const Image &image : images_) out.media.push_back(image.medium);
        out.selected = selected_;
        out.ejected = ejected_;
        out.eject_seen = eject_seen_;
        out.dirty = dirty_;
        out.in_drive = in_drive_ < 0 ? kNoMedium : uint32_t(in_drive_);
        return out;
    }
    // A snapshot fits this tray when it was taken over the same list of images and says nothing impossible about it. `media` is the size of the manifest.
    bool fits(const Snapshot &snap, uint32_t media) const
    {
        if (snap.media.size() != images_.size() || snap.selected > images_.size()) return false;
        for (size_t i = 0; i < images_.size(); ++i)
            if (snap.media[i] != images_[i].medium) return false;
        return snap.in_drive == kNoMedium || snap.in_drive < media;
    }
    void restore(const Snapshot &snap)
    {
        selected_ = snap.selected;
        ejected_ = snap.ejected;
        eject_seen_ = snap.eject_seen;
        dirty_ = snap.dirty;
        in_drive_ = snap.in_drive == kNoMedium ? -1 : int64_t(snap.in_drive);
    }

private:
    std::vector<Image> images_;
    unsigned selected_ = 0;
    bool ejected_ = false;
    bool eject_seen_ = false;      // the tray was opened since the last boundary
    bool dirty_ = false;           // a request has not been looked at since the last boundary
    int64_t in_drive_ = -1;        // the medium the bridge's drive holds once every event handed over is applied (-1: empty)
};

}  // namespace p6_libretro

#endif
